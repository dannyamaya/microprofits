"""
BillionBot X/Twitter Scraper
Fetches tweets from tracked accounts and stores in SQLite.
Deduplicates by tweet ID — never wastes an API read on a tweet we've seen.

Public accounts use Bearer Token (app-only auth).
Private accounts use OAuth 1.0a (user context) — requires consumer key/secret + access token/secret.

Usage:
    uv run python x_scraper.py              # Fetch latest tweets
    uv run python x_scraper.py --backfill   # Paginate to fill 2 days of history
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx
from requests_oauthlib import OAuth1
import sam_notifier

CONFIG_PATH = Path(__file__).parent / "config.json"
DB_PATH = Path(__file__).parent / "data" / "headlines.db"
DATA_DIR = Path(__file__).parent / "data"
DISCORD_STATE_PATH = Path(__file__).parent / "data" / "discord_posted.json"

# Accounts to follow. Set private=True for accounts that require user-context auth.
ACCOUNTS = {
    "DeItaone": {"id": "2704294333", "name": "Walter Bloomberg", "private": False},
    "jukan05": {"id": "1836240683268759552", "name": "Jukan (Memory/SSD)", "private": False},
    "smarter411": {"id": "1137278262", "name": "Sam Parikh", "private": True},
}


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_bearer_token(config: dict) -> str:
    raw = config.get("x_api", {}).get("bearer_token", "")
    return unquote(raw)


def get_oauth1(config: dict) -> OAuth1 | None:
    x = config.get("x_api", {})
    ck = x.get("consumer_key")
    cs = x.get("consumer_secret")
    at = x.get("access_token")
    ats = x.get("access_token_secret")
    if not all([ck, cs, at, ats]):
        return None
    return OAuth1(ck, cs, at, ats)


def init_db():
    """Create the headlines table if it doesn't exist."""
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS headlines (
            tweet_id TEXT PRIMARY KEY,
            account TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            likes INTEGER DEFAULT 0,
            retweets INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_headlines_created
        ON headlines(created_at DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_headlines_account
        ON headlines(account, created_at DESC)
    """)
    conn.commit()
    return conn


def get_latest_tweet_id(conn: sqlite3.Connection, account: str) -> str | None:
    """Get the most recent tweet ID we have for an account."""
    row = conn.execute(
        "SELECT tweet_id FROM headlines WHERE account = ? ORDER BY created_at DESC LIMIT 1",
        (account,),
    ).fetchone()
    return row[0] if row else None


def store_tweets(conn: sqlite3.Connection, tweets: list[dict], account: str) -> tuple[int, list[dict]]:
    """Store tweets, skip duplicates. Returns (count of new, list of new tweet dicts)."""
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    new_tweets = []
    for t in tweets:
        try:
            metrics = t.get("public_metrics", {})
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO headlines
                   (tweet_id, account, text, created_at, fetched_at, likes, retweets, replies)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t["id"],
                    account,
                    t["text"],
                    t["created_at"],
                    now,
                    metrics.get("like_count", 0),
                    metrics.get("retweet_count", 0),
                    metrics.get("reply_count", 0),
                ),
            )
            if conn.total_changes > before:
                new_count += 1
                new_tweets.append({"account": account, "text": t["text"], "created_at": t["created_at"]})
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return new_count, new_tweets


def fetch_tweets(
    bearer_token: str,
    user_id: str,
    oauth1: OAuth1 | None = None,
    private: bool = False,
    since_id: str | None = None,
    pagination_token: str | None = None,
) -> tuple[list[dict], dict]:
    """Fetch tweets from X API. Returns (tweets, meta).
    Uses OAuth 1.0a for private accounts, Bearer Token for public ones.
    """
    params = {
        "max_results": 100,
        "tweet.fields": "created_at,public_metrics,text",
    }
    if since_id:
        params["since_id"] = since_id
    if pagination_token:
        params["pagination_token"] = pagination_token

    url = f"https://api.x.com/2/users/{user_id}/tweets"

    if private:
        if not oauth1:
            raise RuntimeError("OAuth 1.0a credentials required for private accounts — add consumer_key/secret + access_token/secret to config.json")
        import requests
        resp = requests.get(url, auth=oauth1, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        remaining = resp.headers.get("x-rate-limit-remaining", "?")
    else:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        remaining = resp.headers.get("x-rate-limit-remaining", "?")

    print(f"  API reads remaining: {remaining}")
    return data.get("data", []), data.get("meta", {})


# Users to mention in Discord when specific accounts post new tweets
MENTION_ON_NEW: dict[str, list[str]] = {
    "smarter411": ["<@478396479707283457>"],
}


def load_discord_state() -> dict[str, str]:
    """Load last-posted tweet IDs per account. Returns {username: tweet_id}."""
    if DISCORD_STATE_PATH.exists():
        return json.loads(DISCORD_STATE_PATH.read_text())
    return {}


def save_discord_state(state: dict[str, str]):
    DISCORD_STATE_PATH.parent.mkdir(exist_ok=True)
    DISCORD_STATE_PATH.write_text(json.dumps(state, indent=2))


def post_to_discord(webhook_url: str, new_counts: dict[str, int], new_tweet_list: list[dict], only_if_new: bool = False):
    """Post a scrape summary to Discord via webhook.
    Uses discord_posted.json to track last-posted tweet ID per account — no double posts.
    """
    if not webhook_url:
        return

    if only_if_new and not any(c > 0 for c in new_counts.values()):
        return

    # Filter to only tweets not yet posted to Discord
    state = load_discord_state()
    unseen: list[dict] = []
    latest_ids: dict[str, str] = {}
    first_run_accounts: set[str] = set()

    for t in new_tweet_list:
        acct = t["account"]
        if acct not in state:
            # First time we've seen this account — bootstrap state, don't post
            first_run_accounts.add(acct)
        else:
            last_posted_ts = state[acct]
            if t["created_at"] > last_posted_ts:
                unseen.append(t)
        if t["created_at"] > latest_ids.get(acct, ""):
            latest_ids[acct] = t["created_at"]

    # Save bootstrapped state for first-run accounts
    if first_run_accounts:
        new_state = {**state, **{k: v for k, v in latest_ids.items() if k in first_run_accounts}}
        save_discord_state(new_state)
        state = new_state
        print(f"  Discord state bootstrapped for: {', '.join(first_run_accounts)}")

    if only_if_new and not unseen:
        return

    mentions = []
    for username in {t["account"] for t in unseen}:
        if username in MENTION_ON_NEW:
            mentions.extend(MENTION_ON_NEW[username])
    mention_str = " ".join(set(mentions))

    parts = []
    for username, count in new_counts.items():
        name = ACCOUNTS[username]["name"]
        parts.append(f"**{name}** {count} new")

    header = "📡 " + " · ".join(parts)
    if mention_str:
        header = f"{mention_str} {header}"

    top_lines = "\n".join(f"> {t['text'][:140]}" for t in unseen[:5])
    content = f"{header}\n{top_lines}" if top_lines else header

    try:
        resp = httpx.post(webhook_url, json={"content": content}, timeout=10)
        resp.raise_for_status()
        # Only update state after a successful post
        new_state = {**state, **latest_ids}
        save_discord_state(new_state)
    except Exception as e:
        print(f"  Discord webhook failed: {e}")


def cleanup_old(conn: sqlite3.Connection, days: int = 3):
    """Delete headlines older than N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    deleted = conn.execute(
        "DELETE FROM headlines WHERE created_at < ?", (cutoff,)
    ).rowcount
    conn.commit()
    if deleted:
        print(f"  Cleaned up {deleted} headlines older than {days} days")


def export_recent(conn: sqlite3.Connection, hours: int = 24) -> list[dict]:
    """Export recent headlines as a list of dicts for the analysis pipeline."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        """SELECT tweet_id, account, text, created_at, likes, retweets
           FROM headlines
           WHERE created_at >= ?
           ORDER BY created_at DESC""",
        (cutoff,),
    ).fetchall()

    return [
        {
            "id": r[0],
            "account": r[1],
            "text": r[2],
            "created_at": r[3],
            "likes": r[4],
            "retweets": r[5],
        }
        for r in rows
    ]


def run(backfill: bool = False, only_account: str | None = None):
    config = load_config()
    bearer_token = get_bearer_token(config)
    if not bearer_token:
        print("ERROR: No X API bearer token in config.json")
        sys.exit(1)

    oauth1 = get_oauth1(config)
    webhook_url = config.get("discord_webhook", "")

    accounts = {k: v for k, v in ACCOUNTS.items() if only_account is None or k == only_account}
    if only_account and not accounts:
        print(f"ERROR: Unknown account '{only_account}'")
        sys.exit(1)

    conn = init_db()
    now = datetime.now(timezone.utc)
    total_new = 0
    new_counts: dict[str, int] = {}
    all_new_tweets: list[dict] = []

    for username, info in accounts.items():
        is_private = info.get("private", False)
        print(f"\n[{now.isoformat()}] Fetching @{username} ({info['name']}){'  [private]' if is_private else ''}...")

        if backfill:
            pagination_token = None
            page = 0
            while page < 10:
                tweets, meta = fetch_tweets(bearer_token, info["id"], oauth1=oauth1, private=is_private, pagination_token=pagination_token)
                if not tweets:
                    break
                new, new_twts = store_tweets(conn, tweets, username)
                total_new += new
                new_counts[username] = new_counts.get(username, 0) + new
                all_new_tweets.extend(new_twts)
                print(f"  Page {page + 1}: {len(tweets)} fetched, {new} new")
                pagination_token = meta.get("next_token")
                if not pagination_token:
                    break
                page += 1
        else:
            since_id = get_latest_tweet_id(conn, username)
            if since_id:
                print(f"  Fetching since tweet {since_id}...")
            tweets, meta = fetch_tweets(bearer_token, info["id"], oauth1=oauth1, private=is_private, since_id=since_id)
            new, new_twts = store_tweets(conn, tweets, username)
            total_new += new
            new_counts[username] = new
            all_new_tweets.extend(new_twts)
            print(f"  Fetched {len(tweets)} tweets, {new} new")

    # Cleanup old data
    cleanup_old(conn, days=3)

    # For Sam: use AI summarizer instead of raw post
    sam_new = [t for t in all_new_tweets if t["account"] == "smarter411"]
    non_sam_new = [t for t in all_new_tweets if t["account"] != "smarter411"]
    non_sam_counts = {k: v for k, v in new_counts.items() if k != "smarter411"}

    if sam_new:
        sam_notifier.notify(sam_new)

    # Post to Discord for non-Sam accounts (always for full runs, only if new for per-account)
    if non_sam_counts or (only_account is None):
        post_to_discord(webhook_url, non_sam_counts or new_counts, non_sam_new or all_new_tweets, only_if_new=only_account is not None)

    # Export recent headlines to JSON for the analysis pipeline
    recent = export_recent(conn, hours=48)
    export_path = DATA_DIR / "headlines.json"
    with open(export_path, "w") as f:
        json.dump({"exported_at": now.isoformat(), "headlines": recent}, f, indent=2)

    # Summary
    total = conn.execute("SELECT COUNT(*) FROM headlines").fetchone()[0]
    print(f"\n{'='*50}")
    print(f"  Total new headlines: {total_new}")
    print(f"  Total in DB: {total}")
    print(f"  Recent (48h) exported: {len(recent)}")
    print(f"  Output: {export_path}")
    print(f"{'='*50}")

    # Show latest 5
    print("\n  Latest headlines:")
    for h in recent[:5]:
        print(f"  [{h['created_at'][:16]}] {h['text'][:100]}")

    conn.close()


if __name__ == "__main__":
    backfill = "--backfill" in sys.argv
    only_account = None
    for arg in sys.argv[1:]:
        if arg.startswith("--account="):
            only_account = arg.split("=", 1)[1]
    run(backfill=backfill, only_account=only_account)
