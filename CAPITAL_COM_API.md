# Capital.com API — Technical Reference

Practical integration notes derived from building live trading engines (Spider + Microprofits) on top of the Capital.com REST API. Covers authentication, sessions, candle data, order placement, position management, and all the non-obvious gotchas.

---

## Base URLs

```
Demo:  https://demo-api-capital.backend-capital.com
Live:  https://api-capital.backend-capital.com
```

**Note:** Demo never worked reliably for our account. We develop and test against live with a small dedicated account ($1,000 balance). API keys created on a live account only work on the live URL, not demo.

---

## Authentication

### Credentials

| Field | Description |
|-------|-------------|
| `X-CAP-API-KEY` | API key — created in Capital.com dashboard under API Management |
| `identifier` | Account email address |
| `password` | API key password (set when creating the key, NOT your login password) |

### Session creation

```
POST /api/v1/session
Headers: X-CAP-API-KEY, Content-Type: application/json
Body:    { "identifier": "<email>", "password": "<api_key_password>", "encryptedPassword": false }
```

Response headers (extract these — they are your auth tokens for all subsequent calls):
```
CST: <client-session-token>
X-SECURITY-TOKEN: <security-token>
```

Every subsequent request must include both headers:
```
CST: <token>
X-SECURITY-TOKEN: <token>
```

### Password special characters gotcha

**API key passwords with `!!` or other shell metacharacters will break if passed through bash** (e.g., `curl -d '...'`). Bash history expansion eats `!!` even inside single quotes in some configurations. Workaround: write the JSON body to a file first, then use `curl -d @file.json`. Python `httpx`/`requests` handles this correctly since it reads from the `.env` file without shell interpretation.

### Session expiry

- Tokens expire after **10 minutes of inactivity**
- Proactively refresh at 8 minutes to avoid mid-request expiry
- On 401 response: re-authenticate immediately and retry the request once
- Track `last_used` timestamp on every request, not just auth calls

### Multiple accounts (same login)

One login can have multiple trading accounts (e.g., demo + live, or multiple live accounts).

To activate a specific account for trading:
```
PUT /api/v1/session
Body: { "accountId": "<account_id>" }
```

This switches the session globally — all subsequent calls operate on this account. If you need two accounts active simultaneously, you must run two independent sessions (two separate auth flows, two sets of CST + X-SECURITY-TOKEN).

**Account selection pitfall:** `GET /api/v1/accounts` returns ALL accounts, and `accounts[0]` may be an empty/inactive one ($0 balance). Always filter by `accountId` or pick the account with the highest balance. The `preferred: true` flag is not reliable — it may point to an old default account.

To list all accounts:
```
GET /api/v1/accounts
```
Response:
```json
{
  "accounts": [
    {
      "accountId": "314123535689003294",
      "accountName": "demoprod",
      "preferred": true,
      "accountType": "CFD",
      "balance": {
        "balance": 9009.45,
        "deposit": 5000.0,
        "profitLoss": 4009.45,
        "available": 9009.45
      }
    }
  ]
}
```

---

## Instruments (Epics)

Capital.com uses **epic** as the instrument identifier (e.g., `US100`, `MU`, `NVDA`).

### Search instruments
```
GET /api/v1/markets?searchTerm=<query>
```

### Get instrument details (min/max size, margin factor, spread)
```
GET /api/v1/markets/<epic>
```

Key fields:
```json
{
  "instrument": {
    "epic": "US100",
    "instrumentName": "US Tech 100",
    "minDealSize": 1,
    "maxDealSize": 9999,
    "marginFactor": 0.05,
    "lotSize": 1
  },
  "dealingRules": {
    "minDealSize": { "value": 1 },
    "minStopOrProfitDistance": { "value": 10 }
  },
  "snapshot": {
    "bid": 23590.0,
    "offer": 23591.5,
    "change": 45.2,
    "changePct": 0.19
  }
}
```

**Important:** `bid` = price to sell (short). `offer`/`ask` = price to buy (long). Spread = offer - bid.

### minStopOrProfitDistance — not always what it seems

The `dealingRules.minStopOrProfitDistance` value from the API returned `0.01` for US100 in our testing (March 2026). This is essentially no minimum — you can set very tight stops. The documented "typically 5-15 pts for indices" appears to be outdated or applies to guaranteed stops only. **Always query this value at runtime** rather than hardcoding; it may change per instrument or over time.

---

## Historical Candle Data

```
GET /api/v1/prices/<epic>?resolution=<res>&max=<n>
```

### Resolutions

| Parameter | Description |
|-----------|-------------|
| `MINUTE` | 1-minute candles |
| `MINUTE_5` | 5-minute |
| `MINUTE_15` | 15-minute |
| `MINUTE_30` | 30-minute |
| `HOUR` | 1-hour |
| `HOUR_4` | 4-hour |
| `DAY` | Daily |
| `WEEK` | Weekly |

### Parameters

| Param | Description |
|-------|-------------|
| `resolution` | Candle timeframe (see above) |
| `max` | Number of candles to return (max ~1000) |
| `from` | ISO datetime filter start |
| `to` | ISO datetime filter end |

### Response shape

```json
{
  "prices": [
    {
      "snapshotTime": "2026/03/26 14:30:00",
      "snapshotTimeUTC": "2026-03-26T19:30:00",
      "openPrice":  { "bid": 23580.0, "ask": 23581.5, "lastTraded": null },
      "highPrice":  { "bid": 23605.0, "ask": 23606.5, "lastTraded": null },
      "lowPrice":   { "bid": 23570.0, "ask": 23571.5, "lastTraded": null },
      "closePrice": { "bid": 23598.0, "ask": 23599.5, "lastTraded": null },
      "lastTradedVolume": 1842
    }
  ]
}
```

### Critical timestamp gotcha

**`snapshotTime` is NOT UTC.** It reflects the Capital.com server timezone, which can vary. Always use `snapshotTimeUTC` if you need true UTC, or parse `snapshotTime` as naive local time and handle timezone separately.

Supported parse formats seen in production:
- `%Y/%m/%d %H:%M:%S` — most common
- `%Y-%m-%dT%H:%M:%S` — alternate (ISO 8601)
- `%Y-%m-%dT%H:%M` — without seconds

**All three formats appear in production.** The spider project ran blind for 13+ hours because the parser only handled the first format. Always implement multi-format parsing.

### Which price field to use

- For **entry price reference / candle analysis**: use `ask` (offer side) — this is what you pay to buy
- For **BUY positions**: candle body = `ask.close - ask.open`
- For **SELL positions**: candle body = `bid.open - bid.close`
- Volume (`lastTradedVolume`) is available but thin on CFDs — treat as relative indicator only

### The last candle is always open (live)

When polling candles, the API always returns the current in-progress candle as the last element. To get only closed candles:
```python
closed_candles = raw[:-1]  # all except last = confirmed closed
current_open   = raw[-1]   # last = still forming
```

For entry signals based on closed candles: use `raw[-2]` (last confirmed close).
For scalping/momentum where you compare live price to previous close: use `raw[-1].close` vs `raw[-2].close`.

---

## Opening Positions

```
POST /api/v1/positions
Body:
{
  "epic": "US100",
  "direction": "BUY",       // or "SELL"
  "size": 3,
  "stopLevel": 23570.0,     // optional — absolute price level
  "profitLevel": 23650.0,   // optional — absolute price level
  "guaranteedStop": false   // true = guaranteed stop (costs premium)
}
```

### Trailing stop strategy — omit profitLevel

When implementing a trailing stop strategy, **do not set `profitLevel`** on the order. If you set it, Capital.com will auto-close the position when price hits that level, preventing your trailing logic from letting winners run. Instead:
- Set only `stopLevel` at entry
- Monitor `upl` from `GET /api/v1/positions` every poll cycle
- Move `stopLevel` up via `PUT /api/v1/positions/<dealId>` as profit grows
- Capital.com enforces the SL server-side — your position is protected even if your bot crashes

### Two-step deal confirmation

The API does NOT return fill details directly. It returns a `dealReference`:
```json
{ "dealReference": "0000...-abc123" }
```

You must then poll for confirmation:
```
GET /api/v1/confirms/<dealReference>
```

Response:
```json
{
  "dealId": "00000000-4bd9-08...",
  "dealReference": "0000...-abc123",
  "dealStatus": "OPEN",
  "reason": "SUCCESS",
  "level": 23584.30,
  "size": 3,
  "direction": "BUY"
}
```

- `dealStatus`: `OPEN` = filled, `REJECTED` = failed
- `reason`: on rejection this contains the error code (e.g., `MARKET_CLOSED`, `INSUFFICIENT_FUNDS`, `MIN_DEAL_SIZE_NOT_MET`)
- The confirm endpoint may return 404 briefly before the deal is processed — retry 2-3 times with 1s delay

### Deal ID reconciliation gotcha

**The `dealId` from `/confirms/<ref>` may differ from the `dealId` in `GET /api/v1/positions`.** After opening a position, wait ~1 second, then call `GET /api/v1/positions` and match by epic + direction to get the REAL deal ID. Store this reconciled deal ID for all future updates and closes. Using the wrong deal ID will cause silent failures.

### Common rejection reasons

| reason | Cause |
|--------|-------|
| `MARKET_CLOSED` | Outside trading hours for this instrument |
| `INSUFFICIENT_FUNDS` | Not enough available margin |
| `MIN_DEAL_SIZE_NOT_MET` | `size` below instrument minimum |
| `STOP_OR_LIMIT_NOT_ALLOWED` | SL/TP outside allowed distance range |
| `PRICE_TOLERANCE_EXCEEDED` | Slippage too large (rarely seen on CFDs) |

### SL/TP constraints

- SL and TP are **absolute price levels**, not distances
- Capital.com enforces a minimum distance between entry and SL/TP (query `dealingRules.minStopOrProfitDistance`)
- For US100 this was `0.01` in practice (essentially no minimum), but always check at runtime
- Violations return `STOP_OR_LIMIT_NOT_ALLOWED` on confirmation

---

## Modifying Positions (Update SL/TP)

```
PUT /api/v1/positions/<dealId>
Body:
{
  "stopLevel": 23590.0,
  "guaranteedStop": false
}
```

Same two-step confirmation pattern — returns `dealReference`, then poll `/confirms/<ref>`.

### Fire-and-forget SL updates

For trailing stop updates where speed matters more than confirmation, you can skip the confirmation polling. The `PUT` request itself is sufficient — Capital.com processes it immediately. The confirmation just tells you the result. In a trailing scenario where you're moving the SL up every few seconds, waiting 1-3 seconds for confirmation on each move adds unacceptable latency. Just fire the PUT and move on. If it silently fails, the next poll will detect the SL hasn't moved and retry.

---

## Closing Positions

```
DELETE /api/v1/positions/<dealId>
Body: {}   // empty body required for full close
```

For partial close:
```
DELETE /api/v1/positions/<dealId>
Body: { "size": 1 }   // close 1 of N contracts
```

Same confirmation pattern applies.

---

## Reading Open Positions

```
GET /api/v1/positions
```

Response:
```json
{
  "positions": [
    {
      "position": {
        "dealId": "00000000-4bd9-...",
        "direction": "BUY",
        "size": 3,
        "level": 23584.30,
        "stopLevel": 23570.0,
        "profitLevel": null,
        "upl": 45.60,
        "currency": "USD"
      },
      "market": {
        "epic": "US100",
        "instrumentName": "US Tech 100",
        "bid": 23599.50,
        "offer": 23601.00
      }
    }
  ]
}
```

- `level` = fill price (entry)
- `upl` = unrealized P&L in account currency (live, updates every poll)
- `stopLevel` / `profitLevel` = current SL/TP levels (null if not set)
- The `market` block gives live bid/offer — use for real-time P&L display

### UPL for trailing stops

`upl` is the key field for trailing stop logic. For a BUY position with 1 contract on US100, `upl` directly equals the point movement in dollars ($1/point). Use it to determine when to move the stop:

```python
if live.upl >= profit_target:
    # Move SL up by profit_target points
    new_sl = entry_price + (locks * tp_distance)
    await client.update_position_fast(deal_id, stop_level=new_sl)
```

---

## Trade History

```
GET /api/v1/history/transactions?type=TRADE&maxSpanInSeconds=<seconds>
```

Returns closed trades within the time window. `maxSpanInSeconds` is an integer.

---

## Error Codes

| HTTP | errorCode | Meaning |
|------|-----------|---------|
| 400 | `error.invalid.input` | Malformed request body |
| 400 | `error.invalid.request` | Valid API key but wrong password, or malformed credentials |
| 401 | `error.invalid.api.key` | API key not recognized (wrong key, not wrong password) |
| 403 | `error.not.authorised` | Account lacks permissions for this action |
| 404 | — | Resource not found (deal not yet processed — retry) |
| 429 | — | Rate limited — back off and retry |
| 503 | — | Capital.com maintenance / outage |

**Distinguishing 400 vs 401:** A `401` with `error.invalid.api.key` means the key itself is wrong. A `400` with `error.invalid.request` means the key is valid but the password is wrong or the body is malformed. This distinction matters when debugging auth issues.

Error response body:
```json
{ "errorCode": "error.invalid.api.key" }
```

### Error code 10197 — Competing session

```
error.invalid.details.deal.applicationRefresh.clientId-10197
```

Occurs when the same Capital.com account is active on TWS or another session simultaneously. The API will switch to **delayed market data** automatically (type 3 instead of type 1). This is normal when a live account is open elsewhere — handle it gracefully rather than treating it as a fatal error.

---

## Rate Limits

No official published limit, but in practice:
- **10 requests/second** is safe for continuous polling
- A 3-second poll interval with 2-3 requests per cycle (candles + positions + occasional SL update) is sustainable
- Burst of 20-30 requests triggers 429 — implement exponential backoff
- Each candle refresh call counts as one request regardless of how many candles returned
- Fire-and-forget SL updates (no confirmation polling) save 1-3 requests per trail move

---

## Candle Polling Pattern

Capital.com has no WebSocket for candle data — you must poll REST.

Recommended approach:
1. On startup: bulk-load historical candles with `max=30` (or whatever lookback you need for indicators like EMA)
2. Every 3-5 seconds: fetch `max=3` (enough to catch the latest closed + current open)
3. Compare timestamps to detect new closed candles — only process candles with `timestamp > last_known_ts`
4. The last element is always the open (forming) candle — use it for live price comparison, not for confirmed signals

```python
raw = await client.get_prices(epic, "MINUTE", max_bars=3)
closed = raw[:-1]   # confirmed closed candles
current = raw[-1]    # live/forming candle

for candle in closed:
    if candle.timestamp > last_known_ts:
        history.push(candle)

# For momentum scalping: compare current live price to last close
if current.close > history.last.close:
    # Price is above previous close — potential entry
```

---

## Position Monitoring Pattern

Capital.com has no push notifications for SL/TP hits. You must poll `GET /api/v1/positions` to detect when a position closes server-side.

```python
live_positions = await client.get_positions()
live_deal_ids = {p.deal_id for p in live_positions}

for deal_id, tracked in tracked_positions.items():
    if deal_id not in live_deal_ids:
        # Position closed server-side (SL or TP hit)
        handle_server_close(tracked)
```

Poll frequency: every 3-5 seconds for scalping bots, every 15-30 seconds for swing strategies.

### Crash recovery

On restart, load open trades from your database and reconcile against `GET /api/v1/positions`:
- Position in DB AND on broker: recover it, resume tracking
- Position in DB but NOT on broker: SL/TP fired while bot was down — mark as closed, estimate P&L from known SL/TP levels
- Position on broker but NOT in DB: orphan — log it but don't touch

Since SL is set server-side on every position, the broker protects you even if the bot is completely down.

---

## Key Non-Obvious Behaviors

1. **`snapshotTime` is not UTC** — always use `snapshotTimeUTC` or convert explicitly
2. **Three timestamp formats in production** — parser must handle `%Y/%m/%d %H:%M:%S`, `%Y-%m-%dT%H:%M:%S`, and `%Y-%m-%dT%H:%M`
3. **Last candle in response is always the current open candle** — never use it for confirmed signals, but it IS useful for live price comparison in scalping
4. **Deal confirmation is async** — `POST /positions` returns a reference, not a fill; always confirm via `/confirms/<ref>`
5. **Deal ID from confirmation may differ from live positions** — always reconcile by fetching `GET /positions` after opening
6. **Account session is global** — switching accounts with `PUT /session` affects all requests on that client instance; two accounts = two independent clients with separate auth tokens
7. **`accounts[0]` may be empty** — always filter by account ID or max balance, not array index
8. **SL/TP are absolute levels, not offsets** — `stopLevel: 23570` not `stopLevel: -15`
9. **Minimum stop distance for US100 is 0.01** — essentially no minimum, contrary to what documentation suggests. Always query at runtime.
10. **For trailing stops, omit profitLevel** — setting it causes Capital.com to auto-close at that level, defeating the trail
11. **SL updates can be fire-and-forget** — skip confirmation polling for speed; the PUT itself is processed immediately
12. **`/confirms/<ref>` may 404 briefly** — retry 2-3 times with 1s delay before treating as failure
13. **`upl` in positions is live P&L** — updated every poll, suitable for real-time display and trailing stop logic
14. **Closing a position with `DELETE` requires an empty `{}` body** — omitting the body causes errors on some HTTP clients
15. **Volume on CFDs is very thin** — `lastTradedVolume` exists but is not comparable to exchange volume; use as a relative signal only
16. **Passwords with `!!` break bash** — shell history expansion eats double bangs; write JSON to file or use a language that reads from env directly
17. **`error.invalid.request` (400) means wrong password** — distinct from `error.invalid.api.key` (401) which means wrong key. Both feel like "auth failed" but the fix is different.
18. **`deposit` in account balance is total capital deposited, NOT margin in use** — actual margin in use = `balance - available`
