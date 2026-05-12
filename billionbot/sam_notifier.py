"""
Sam Notifier — Summarizes new Sam Parikh tweets for Dan via Claude Haiku.
Called by x_scraper.py when new smarter411 tweets arrive.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

CONFIG_PATH = Path(__file__).parent / "config.json"
# Set CLAUDE_CODE_OAUTH_TOKEN in environment or config.json "claude_oauth_token"
DAN_MENTION = "<@478396479707283457>"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def summarize_tweets(tweets: list[dict]) -> str:
    tweet_lines = "\n".join(f"[{t['created_at'][11:16]}] {t['text']}" for t in tweets)

    prompt = f"""You are a real-time trading assistant. Sam Parikh (@smarter411) just posted the following:

{tweet_lines}

Write a 2-3 line summary for a trader:
1. What is Sam calling? (direction, instrument, level if given)
2. Any actionable signal or key level to watch?

Be direct and specific. No filler. Under 60 words."""

    config = load_config()
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or config.get("claude_oauth_token", "")
    env = {**os.environ}
    if oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

    result = subprocess.run(
        ["claude", "--model", "claude-haiku-4-5-20251001", "--print", prompt],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:200]}")
    return result.stdout.strip()


def post_to_discord(webhook_url: str, tweets: list[dict], summary: str):
    raw_lines = "\n".join(f"> {t['text'][:120]}" for t in tweets[:3])
    content = f"{DAN_MENTION} **Sam just posted:**\n{raw_lines}\n\n💬 _{summary}_"
    resp = httpx.post(webhook_url, json={"content": content}, timeout=10)
    resp.raise_for_status()


def notify(tweets: list[dict]):
    """Main entry point. Called with list of new tweet dicts."""
    if not tweets:
        return

    config = load_config()
    webhook_url = config.get("discord_webhook", "")
    if not webhook_url:
        print("  No webhook configured, skipping Sam notification")
        return

    try:
        summary = summarize_tweets(tweets)
        post_to_discord(webhook_url, tweets, summary)
        print(f"  Sam notifier: posted summary for {len(tweets)} tweet(s)")
    except Exception as e:
        print(f"  Sam notifier failed: {e}")
        # Fallback: post raw tweets without summary
        try:
            raw_lines = "\n".join(f"> {t['text'][:120]}" for t in tweets[:3])
            content = f"{DAN_MENTION} **Sam just posted:**\n{raw_lines}"
            httpx.post(webhook_url, json={"content": content}, timeout=10)
        except Exception:
            pass


if __name__ == "__main__":
    # For testing: pass tweet text as argv
    test_tweets = [{"text": " ".join(sys.argv[1:]) or "mu 830 calls at 3 beast", "created_at": "2026-05-12T16:00:00.000Z"}]
    notify(test_tweets)
