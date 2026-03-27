# Microprofits

Aggressive US100 scalping bot with trailing stop loss, integrated with Capital.com REST API.

## What It Does

Detects upward momentum on 1-minute candles, opens BUY positions with SL only (no TP), then trails the SL up in profit_target increments as price runs. Breakeven protection kicks in at half the profit target.

### Trail Sequence (profit_target = $5)

```
Entry @ 19,580       SL = 19,575  (-$5)      Trail: —
UPL >= $2.50    →    SL = 19,580  ($0)       Trail: BE (breakeven)
UPL >= $5.00    →    SL = 19,585  (+$5)      Trail: 1x
UPL >= $10.00   →    SL = 19,590  (+$10)     Trail: 2x
...
Price reverses  →    SL hit at last lock level
```

### Entry Logic

1. Current candle price > previous closed candle close
2. Optional EMA slope filter (switchable from dashboard)
3. Entry cooldown between positions (configurable, default 30s)

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
├── strategy/      Scalper entry logic, EMA filter, candle history
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
- **Account**: `microprofits` (ID: `315494510724722974`)
- **Email**: `danny.amaya92@gmail.com`
- **API key and password**: in `.env` (never committed)
- **Session tokens**: expire after 10min idle, auto-refresh at 8min
- **Key gotchas**: see `CAPITAL_COM_API.md` for full reference

## Database Tables

| Table | Purpose |
|-------|---------|
| `bot_config` | Singleton config row (profit_target, stop_loss, max_positions, etc.) |
| `symbol_config` | Per-instrument overrides (US100 enabled by default) |
| `trades` | Every position open + close with P&L |
| `audit_log` | All bot decisions (ENTRY, BREAKEVEN, TRAIL_MOVE, SL_HIT, etc.) |

## Configuration (all editable from dashboard)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `profit_target` | $5 | Trail jump size and breakeven reference |
| `stop_loss` | $5 | Initial SL distance in dollars |
| `max_positions` | 3 | Max concurrent positions per symbol |
| `num_contracts` | 1 | Contracts per position |
| `entry_cooldown` | 30s | Min time between fresh entries |
| `ema_filter_on` | true | Require EMA slope positive for entry |
| `ema_period` | 5 | EMA lookback (on 1-minute candles) |
| `poll_interval` | 3s | Seconds between each poll cycle |

## Key Design Decisions

- **No profitLevel on orders** — Capital.com would auto-close at TP, defeating the trail. SL only, bot manages exit via trailing.
- **Fire-and-forget SL updates** — `PUT /positions/{id}` without confirmation polling. Saves 1-3s per trail move. Status checked via HTTP response code.
- **60s backoff on order rejection** — prevents spam when margin is insufficient.
- **Breakeven at half target** — eliminates full SL risk early. After breakeven, worst case is $0 not -$10.
- **Independent position tracking** — each position has its own `trail_locks` counter, entry price, and SL level.
- **Crash recovery** — on restart, reconciles DB trades vs live Capital.com positions. SL is server-side so positions are protected even if bot is down.

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
