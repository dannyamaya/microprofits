"""
Morning Dispatch — Daily bias summary at 12:00 UTC
Reads recent tweet data, runs claude --print to generate a structured bias,
stores in Supabase, posts to Discord webhook.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg2

CONFIG_PATH = Path(__file__).parent / "config.json"
DATA_DIR = Path(__file__).parent / "data"

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_supabase_url(config: dict) -> str:
    import os
    return os.environ.get("SUPABASE_URL") or config.get("supabase_url", "")


def get_recent_tweets(hours: int = 20) -> list[dict]:
    headlines_path = DATA_DIR / "headlines.json"
    if not headlines_path.exists():
        return []
    data = json.loads(headlines_path.read_text())
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return [h for h in data["headlines"] if h["created_at"] >= cutoff]


def format_tweets_for_prompt(tweets: list[dict]) -> str:
    by_account: dict[str, list] = {}
    for t in tweets:
        by_account.setdefault(t["account"], []).append(t)

    sections = []
    labels = {
        "DeItaone": "Walter Bloomberg (@DeItaone) — macro/geopolitical wire",
        "jukan05": "Jukan (@jukan05) — memory/semis specialist",
        "smarter411": "Sam Parikh (@smarter411) — active trader calls",
    }
    for account, label in labels.items():
        acct_tweets = by_account.get(account, [])
        if not acct_tweets:
            continue
        lines = [f"## {label}"]
        for t in acct_tweets[:40]:
            ts = t["created_at"][11:16]
            lines.append(f"[{ts}] {t['text'][:200]}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def run_claude(prompt: str) -> str:
    import os
    config = load_config()
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or config.get("claude_oauth_token", "")
    env = {**os.environ}
    if oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

    result = subprocess.run(
        ["claude", "--print", prompt],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:300]}")
    return result.stdout.strip()


def post_to_discord(webhook_url: str, content: str):
    if not webhook_url:
        print("No webhook URL configured, skipping Discord post")
        return
    chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
    for chunk in chunks:
        resp = httpx.post(webhook_url, json={"content": chunk}, timeout=10)
        resp.raise_for_status()


def run():
    config = load_config()
    webhook_url = config.get("discord_webhook_dispatch") or config.get("discord_webhook", "")
    now_utc = datetime.now(timezone.utc)

    print(f"[{now_utc.isoformat()}] Running morning dispatch...")

    tweets = get_recent_tweets(hours=20)
    if not tweets:
        print("No recent tweets found — skipping dispatch")
        return

    tweet_text = format_tweets_for_prompt(tweets)
    date_str = now_utc.strftime("%B %d, %Y")

    prompt = f"""You are a markets analyst writing a pre-market bias for a trader focused on NQ futures (Nasdaq 100).

Today is {date_str}. Here are tweets from the last 20 hours from three tracked sources:

{tweet_text}

Your response must be valid JSON with this exact structure:
{{
  "summary": "<full morning bias text, under 400 words, markdown formatted>",
  "macro_bias": <float -1.0 to 1.0>,
  "semis_bias": <float -1.0 to 1.0>,
  "trader_bias": <float -1.0 to 1.0>,
  "composite_bias": <float, weighted: macro*0.4 + trader*0.4 + semis*0.2>,
  "bias_reasoning": "<one sentence explaining the composite score>"
}}

Bias scale anchors:
+1.0 = strong bull (Fed cut, ceasefire, major beat, clear breakout)
+0.5 = mild tailwind (cautiously long, support holding, constructive tone)
 0.0 = neutral / conflicted signals
-0.5 = mild headwind (fade rallies, wait for setup, cautious tone)
-1.0 = strong bear (rate hike signal, war escalation, major miss, breakdown)

Macro bias = Walter's headlines (Fed, Iran/oil, geopolitics, economic data)
Semis bias = Jukan's signals (memory prices, supply/demand, key names)
Trader bias = Sam's explicit calls + Walter's and Jukan's directional tone on equities

Be direct. No disclaimers. Write for a trader.

Return ONLY the JSON object, no other text."""

    print(f"  Fetched {len(tweets)} tweets, calling claude --print...")

    try:
        raw = run_claude(prompt)
    except Exception as e:
        print(f"  Claude call failed: {e}")
        sys.exit(1)

    # Parse structured JSON response
    try:
        # Strip markdown code fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
            clean = clean.rsplit("```", 1)[0].strip()
        result = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  JSON parse failed: {e}\nRaw: {raw[:200]}")
        # Fallback: treat whole response as summary
        result = {"summary": raw, "macro_bias": None, "semis_bias": None,
                  "trader_bias": None, "composite_bias": None, "bias_reasoning": ""}

    summary = result["summary"]
    composite = result.get("composite_bias")
    macro_b = result.get("macro_bias")
    semis_b = result.get("semis_bias")
    trader_b = result.get("trader_bias")
    reasoning = result.get("bias_reasoning", "")

    print(f"  Summary generated ({len(summary)} chars)")
    if composite is not None:
        bias_label = "🟢 Bullish" if composite > 0.2 else "🔴 Bearish" if composite < -0.2 else "⚪ Neutral"
        print(f"  Composite bias: {composite:+.2f} ({bias_label})")
        print(f"  macro={macro_b:+.2f}  semis={semis_b:+.2f}  trader={trader_b:+.2f}")

    # Store in Supabase
    supabase_url = get_supabase_url(config)
    try:
        conn = psycopg2.connect(supabase_url, sslmode="require")
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO dispatches (date, summary, macro_bias, semis_bias, trader_bias, composite_bias, bias_reasoning)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE SET
                summary = EXCLUDED.summary,
                macro_bias = EXCLUDED.macro_bias,
                semis_bias = EXCLUDED.semis_bias,
                trader_bias = EXCLUDED.trader_bias,
                composite_bias = EXCLUDED.composite_bias,
                bias_reasoning = EXCLUDED.bias_reasoning,
                created_at = NOW()
        """, (now_utc.date(), summary, macro_b, semis_b, trader_b, composite, reasoning))
        conn.commit()
        conn.close()
        print("  Saved to Supabase ✓")
    except Exception as e:
        print(f"  Supabase save failed: {e}")

    # Format Discord message
    bias_bar = ""
    if composite is not None:
        filled = round((composite + 1) * 5)  # 0-10 scale
        bias_bar = f"\n**Bias:** {'🟩' * filled}{'⬜' * (10 - filled)} `{composite:+.2f}` — {reasoning}"

    header = f"🌅 **Morning Dispatch — {date_str}**{bias_bar}\n{'─' * 40}\n"
    full_message = header + summary
    print(f"\n{full_message}\n")

    post_to_discord(webhook_url, full_message)
    print("  Posted to Discord ✓")


if __name__ == "__main__":
    run()
