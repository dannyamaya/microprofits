---
name: fetch-tweets
description: Fetch latest tweets from @DeItaone (Walter Bloomberg) and @jukan05 (Jukan), then upsert daily notes into basic-memory under `tweets/<date>/`. Use when the user says "fetch tweets", "what did Walter say", "what did Jukan say", "update the tweet log", or asks for a recent macro/memory readout from those accounts.
---

# fetch-tweets

Pulls fresh tweets from the two tracked X accounts and persists them as organized daily notes in basic-memory.

## Steps

1. Run the scraper to refresh `data/headlines.json`:
   ```
   uv run python x_scraper.py
   ```
   The scraper deduplicates by tweet id, so re-runs are cheap.

2. Build the per-(date, account) note payloads:
   ```
   uv run python tweet_notes.py
   ```
   This prints a JSON array. Each item has `title`, `directory` (`tweets/<date>`), `tags`, and a fully-formatted `body`.

3. For each item, call `mcp__basic-memory__write_note` with `overwrite=true` so the same day's note gets refreshed when new posts arrive. Project: `main`. Use:
   - `title`: from the payload
   - `directory`: from the payload
   - `tags`: from the payload (extend with topical tags if the day has obvious themes — e.g. `fomc`, `iran`, `hbm`, `dram`)
   - `note_type`: `tweet-log`
   - `content`: the `body` field, optionally appended with a short "## Top themes of the day" section you write yourself based on the posts

4. Reply with a tight summary: per account, the highlights worth knowing. Skip noise replies/RTs unless they carry information. Avoid defaulting to bullish — flag what's priced in, what changed, and what would invalidate the read.

## Notes

- Walter (@DeItaone) is wire-style market headlines: macro data, central bank speak, Trump/geopolitics, single-name catalysts.
- Jukan (@jukan05) is memory/semis specialist: HBM, DRAM, NAND, Korean fabs, hyperscaler capex, supply-chain checks.
- Tweet history older than 3 days is auto-deleted by the scraper (`cleanup_old(days=3)`), so daily notes accumulate the long-term record in basic-memory.
- If the user asks "fetch only X account", filter the payloads accordingly before writing.
