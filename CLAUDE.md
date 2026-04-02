# Microprofits

Multi-strategy trading bot integrated with Capital.com REST API. Supports per-symbol strategy selection from the dashboard.

## Strategies

### 1. Momentum Scalper (US100)

Detects upward momentum on 1-minute candles, opens BUY positions with SL only (no TP), then trails via PctTrailer (85% of peak UPL). No breakeven snap — the pct trail gives trades room to breathe through pullbacks.

#### Trail Sequence (profit_target = $5)

```
Entry @ 19,580       SL = 19,575  (-$5)      Trail: —
UPL >= $2.50    →    SL = 19,580  ($0)       Trail: BE (breakeven)
UPL >= $5.00    →    SL = 19,585  (+$5)      Trail: 1x
UPL >= $10.00   →    SL = 19,590  (+$10)     Trail: 2x
...
Price reverses  →    SL hit at last lock level
```

#### Entry Logic

1. Velocity-based: price must be rising >= `velocity_threshold` pts/sec over `velocity_window` ticks
2. Optional EMA slope filter (switchable from dashboard)
3. **Post-loss cooldown (60s)**: after any SL_HIT, no new entries for 60 seconds. Replaces the old fixed entry_cooldown. Based on analysis showing WR drops from 60% to 33% after a loss.
4. **Schedule filter**: only trades in safe hours (see Operating Schedule)

### 2. Asian Range Breakout (GOLD / XAUUSD)

Marks the high/low of the Asian session (00:00–07:00 UTC), then trades the breakout at London open (08:00–12:00 UTC). Max 1 trade per day.

#### How It Works

```
Asian session (00:00–07:00 UTC):  Record HIGH and LOW
London open (08:00 UTC):
  - Price > Asian HIGH → BUY
  - Price < Asian LOW  → SELL
  - Otherwise          → no trade

SL = opposite end of range
TP = 1.5x range width
Max 1 trade/day. Skip if range > $25 or < $2.
```

#### Key Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Asian window | 00:00–07:00 UTC | Needs ~30+ candles to establish range |
| Breakout window | 08:00–12:00 UTC | London open session |
| TP ratio | 1.5x range | Range width * 1.5 |
| SL | Opposite end of range | Conservative: full range as stop |
| Max range skip | $25 | Volatile day filter |
| Min range skip | $2 | No structure filter |
| Trades per day | 1 | Resets at midnight UTC |

## Tech Stack

- **Backend**: Python 3.11, FastAPI, asyncpg, httpx
- **Frontend**: React 19, Vite, TypeScript
- **Database**: PostgreSQL 16
- **Infrastructure**: Docker Compose (3 containers: backend, frontend, db)

## Project Structure

```
backend/microprofits/
├── api/           Capital.com client (auth, REST, models)
├── config/        Pydantic settings (reads from .env)
├── data/          PostgreSQL store (trades, audit, config)
├── engine/        Main loop + position tracker with trailing SL
├── routes/        FastAPI endpoints (status, config, positions, trades, heatmap)
├── strategy/      Scalper, Asian Range Breakout, EMA filter, candle history
└── main.py        FastAPI app with lifespan

frontend/src/
├── lib/api.ts     Typed fetch wrappers
├── components/    Header, ConfigPanel, PositionTable, PnlSummary, TradeHistory, Heatmap
└── App.tsx        Main dashboard (polls every 5s)
```

## Running Locally

```bash
cp .env.example .env   # fill in Capital.com credentials
docker-compose up --build
# Dashboard: http://localhost:3000
# API: http://localhost:8000/docs
```

## Production (AWS Lightsail)

- **Instance**: Lightsail `small_3_0`, eu-west-2, Ubuntu 22.04
- **Public IP**: `13.41.3.104`
- **Tailscale IP**: `100.101.111.35`
- **Dashboard**: `http://13.41.3.104:3000`
- **API**: `http://13.41.3.104:8000`
- **Server path**: `/opt/microprofits/`
- **SSH**: `ssh ubuntu@100.101.111.35` (via Tailscale)
- **Deploy**: `ssh ubuntu@100.101.111.35 "cd /opt/microprofits && git pull && docker compose up --build -d"`

## Capital.com API

- **Live URL**: `https://api-capital.backend-capital.com`
- **Email**: `danny.amaya92@gmail.com`
- **API key and password**: in `.env` (never committed)
- **Session tokens**: expire after 10min idle, auto-refresh at 8min
- **Key gotchas**: see `CAPITAL_COM_API.md` for full reference

### Accounts (per-symbol)

Each symbol can target a different Capital.com account via `account_id` in `symbol_config`. The bot creates independent API sessions per account.

| Account Name | Account ID | Symbol | Strategy | Budget |
|-------------|-----------|--------|----------|--------|
| `microprofits` | `315494510724722974` | US100 | scalper | ~$9,900 |
| `asian_range` | `315701137306366238` | GOLD | asian_range | $1,000 |

Default account (from `.env` `CAPITAL_ACCOUNT_ID`) is `microprofits`. Symbols with a different `account_id` get their own `RestClient` + `PositionTracker`.

## Operating Schedule

### Scalper (US100) — Safe Hours Only

The scalper restricts entries to hours with historically positive expectancy. Defined in `SCALPER_SAFE_HOURS` in `engine/loop.py`. Based on analysis of ~3,000 trades (Mar-Apr 2026).

| Window | UTC Hours | Colombia (UTC-5) | Why |
|--------|-----------|-------------------|-----|
| **Evening momentum** | 00:00–02:00 | 7:00 PM – 9:00 PM | Session open momentum, +$549 |
| **Late night** | 06:00–07:00 | 1:00 AM – 2:00 AM | Low spread + trend continuation, +$231 |
| **US pre-market + open** | 13:00–17:00 | 8:00 AM – 12:00 PM | Highest volume window, +$185 |
| **US afternoon** | 19:00–20:00 | 2:00 PM – 3:00 PM | Afternoon momentum, +$70 |

**Blocked hours** (negative expectancy): 02-05, 07-12, 17-18, 20-23 UTC. These hours had -$2,554 total loss from choppy price action and wide spreads.

The schedule filter does NOT affect position monitoring — SL/TP hits are still detected 24/7. It only blocks new entries.

### Asian Range (GOLD) — Own Schedule

| Phase | UTC Window | Colombia (UTC-5) | Notes |
|-------|-----------|-------------------|-------|
| **Building range** | 00:00–07:00 | 7:00 PM – 2:00 AM | Records high/low |
| **Breakout window** | 08:00–12:00 | 3:00 AM – 7:00 AM | Entry if breakout |

**For running locally:** start the bot by **6:30 PM Colombia time**. The SL/TP are server-side so even if the PC loses connection after entry, the position is protected.

## Database Tables

| Table | Purpose |
|-------|---------|
| `bot_config` | Singleton config row (profit_target, stop_loss, max_positions, etc.) |
| `symbol_config` | Per-instrument config + strategy selection (US100=scalper, GOLD=asian_range) |
| `trades` | Every position open + close with P&L |
| `audit_log` | All bot decisions (ENTRY, BREAKEVEN, TRAIL_MOVE, SL_HIT, etc.) |

## Configuration (all editable from dashboard)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `profit_target` | $5 | Trail jump size and breakeven reference |
| `stop_loss` | $5 | Initial SL distance in dollars |
| `max_positions` | 3 | Max concurrent positions per symbol |
| `num_contracts` | 1 | Contracts per position |
| `post_loss_cooldown` | 60s | Wait time after SL_HIT before re-entering (hardcoded constant) |
| `ema_filter_on` | true | Require EMA slope positive for entry |
| `ema_period` | 5 | EMA lookback (on 1-minute candles) |
| `poll_interval` | 3s | Seconds between each poll cycle |

## Key Design Decisions

- **Per-symbol strategy + account** — each symbol in `symbol_config` has `strategy` and `account_id` fields. The engine creates independent API sessions per account, so GOLD trades on the `asian_range` account ($1,000) and US100 trades on `microprofits` (~$9,900).
- **No profitLevel on scalper orders** — Capital.com would auto-close at TP, defeating the trail. SL only, bot manages exit via trailing. Asian Range uses TP since it's a fixed-target strategy.
- **Fire-and-forget SL updates** — `PUT /positions/{id}` without confirmation polling. Saves 1-3s per trail move. Status checked via HTTP response code.
- **60s backoff on order rejection** — prevents spam when margin is insufficient.
- **Post-loss cooldown (60s)** — after a losing trade (SL_HIT), the bot pauses 60s before new entries for that epic. Analysis showed WR drops from 60% to 33% after a loss (false momentum persists), so this filters out noise re-entries. Replaces old fixed `entry_cooldown`.
- **Scalper schedule filter** — restricts entries to 8 safe UTC hours (0,1,6,13,14,15,16,19). The other 16 hours had negative expectancy totaling -$2,554. Does not affect position monitoring or Asian Range.
- **No breakeven snap** — removed because it was causing 2-second churn (open → BE snap to $0 → tiny pullback → close at $0 → repeat). The 85% pct trail gives trades room to breathe: at $5 UPL the trail is at -$2.55, at $10 UPL it's at +$1.70. Trades survive normal pullbacks and can develop into bigger winners.
- **Independent position tracking** — each position has its own `trail_locks` counter, entry price, and SL level.
- **Crash recovery** — on restart, reconciles DB trades vs live Capital.com positions. SL is server-side so positions are protected even if bot is down.
- **Rate limit safe** — each symbol fetches only 3 candles per poll (full history only on startup). With 2 symbols at 3s poll: ~4 requests/3s = ~1.3 req/s, well within Capital.com's ~10 req/s safe limit. Each account has its own API session so rate limits are independent.

## Common Operations

```bash
# Check logs
ssh ubuntu@100.101.111.35 "docker logs microprofits-backend-1 --tail 50"

# Restart
ssh ubuntu@100.101.111.35 "cd /opt/microprofits && docker compose restart backend"

# Deploy update
ssh ubuntu@100.101.111.35 "cd /opt/microprofits && git pull && docker compose up --build -d"

# Stop bot (keeps containers running, just disables trading)
curl -X POST http://13.41.3.104:8000/api/bot/stop

# Emergency flatten (close all positions + disable)
curl -X POST http://13.41.3.104:8000/api/bot/flatten
```

## GitHub

- **Repo**: https://github.com/dannyamaya/microprofits.git (private)
- **Branch**: `main`
