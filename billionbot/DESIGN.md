# BillionBot — System Design

## Goal

A real-time market intelligence pipeline that:
1. Ingests tweets from Walter (macro), Jukan (semis), Sam (active trader)
2. Generates a daily directional bias (-1 to +1) stored in Supabase
3. Notifies Dan in Discord with AI-summarized Sam calls (not raw tweets)
4. Exposes the bias so a trading script can execute accordingly

---

## Architecture

```
X API (OAuth 1.0a + Bearer)
        │
        ▼
  x_scraper.py  (cron: */2 for Sam, */30 for all)
        │
        ├──► SQLite (local cache, 3-day rolling)
        │
        ├──► Supabase postgres
        │       ├── tweets          (full history)
        │       ├── dispatches      (daily bias + summary)
        │       └── bias_updates    (intraday bias revisions)
        │
        ├──► sam_notifier.py  (per new Sam tweet)
        │       └── claude --model haiku → contextual summary → Discord @Dan
        │
        └──► morning_dispatch.py  (cron: 0 12 daily)
                └── claude --model sonnet → full bias JSON → Supabase + Discord
```

---

## Components

### 1. `x_scraper.py` (exists)
Polls X API, deduplicates, writes to SQLite + Supabase `tweets`.
- `*/2 * * * *` for Sam (private, OAuth 1.0a)
- `*/30 * * * *` for all accounts

### 2. `sam_notifier.py` (to build)
Triggered whenever x_scraper finds new Sam tweets.
Calls Claude Haiku via `claude --print` to produce a 2-3 line contextual summary:
- What is Sam calling (direction, instrument, level)?
- Why does it matter right now (links to current macro/semis context)?
Posts to Discord and mentions Dan.

### 3. `morning_dispatch.py` (exists, bias scoring added)
Daily at 12 UTC. Reads last 20h of all tweets.
Calls Claude Sonnet via `claude --print` returning structured JSON:
```json
{
  "summary": "...",
  "macro_bias": -0.85,
  "semis_bias": -0.20,
  "trader_bias": -0.65,
  "composite_bias": -0.64,
  "bias_reasoning": "..."
}
```
Stores in `dispatches` table. Posts formatted dispatch to Discord.

### 4. Supabase Schema

```sql
-- Full tweet history
CREATE TABLE tweets (
    tweet_id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    likes INTEGER DEFAULT 0,
    retweets INTEGER DEFAULT 0
);

-- Daily bias (one row per day, updated if re-run)
CREATE TABLE dispatches (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    macro_bias FLOAT,
    semis_bias FLOAT,
    trader_bias FLOAT,
    composite_bias FLOAT,
    bias_reasoning TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Intraday bias revisions (for major Walter headlines)
CREATE TABLE bias_updates (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT NOW(),
    trigger_tweet_id TEXT,
    trigger_account TEXT,
    delta FLOAT,           -- how much bias shifted
    new_composite FLOAT,
    reason TEXT
);
```

### 5. Bias API (for trading script)
The trading script queries Supabase directly:

```python
# Get current bias
import psycopg2
conn = psycopg2.connect(SUPABASE_URL, sslmode='require')
cur = conn.cursor()

# Latest composite bias (morning + any intraday updates)
cur.execute("""
    SELECT composite_bias, bias_reasoning, created_at
    FROM dispatches
    WHERE date = CURRENT_DATE
    ORDER BY created_at DESC LIMIT 1
""")
row = cur.fetchone()
bias = row[0] if row else 0.0  # 0 = neutral if no dispatch today
```

Threshold suggestion:
- `bias > 0.3` → lean long
- `bias < -0.3` → lean short
- `-0.3 to 0.3` → neutral / size down

---

## Skills

### `/fetch-tweets` (exists)
Fetches and saves to basic-memory. Keep as-is.

### `/deploy` (exists)
Rsyncs to gapper, uv sync, smoke test, cron check.
**Extend:** also sync Supabase credentials.

### `/dispatch` (to build)
Manual trigger for morning dispatch. Useful for:
- Running mid-day if macro changes dramatically
- Testing without waiting for cron
```
/dispatch           → runs now, posts to Discord
/dispatch --dry-run → prints output, no post
/dispatch --date 2026-05-10 → re-run for a specific date
```

### `/bias` (to build)
Query and display current bias from Supabase.
```
/bias               → show today's bias + history chart (last 7 days)
/bias update        → re-score bias from latest tweets (without full dispatch)
```

---

## Model Selection

| Task | Model | Why |
|---|---|---|
| Sam tweet summary (real-time) | `claude-haiku-4-5-20251001` | Fast, cheap, good enough for 2-3 line summary |
| Morning dispatch + bias | `claude-sonnet-4-6` | Nuanced macro reading, structured JSON |
| Intraday bias update | `claude-haiku-4-5-20251001` | Triggered frequently, needs to be cheap |

All via `claude --print --model <model>` using `CLAUDE_CODE_OAUTH_TOKEN` on gapper.

---

## Implementation Order

1. **Sam tweet summarizer** — `sam_notifier.py`: replace raw tweet Discord post with Haiku summary
2. **Tweet sync to Supabase** — pipe new tweets to `tweets` table in addition to SQLite
3. **`/bias` skill** — query Supabase, display today's scores
4. **`/dispatch` skill** — manual trigger for morning dispatch
5. **Intraday bias updates** — on major Walter headlines, re-score and write to `bias_updates`
