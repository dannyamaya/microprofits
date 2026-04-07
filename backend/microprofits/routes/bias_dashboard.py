"""
Bias Dashboard API

Endpoints for the /bias frontend page: config, instruments,
positions, trades, stats, audit, and account info.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/bias", tags=["bias-dashboard"])


# -- config ------------------------------------------------------------------

@router.get("/config")
async def get_bias_config(request: Request):
    store = request.app.state.store
    config = await store.get_bias_config()
    result = {k: v for k, v in config.items() if k != "id"}
    # Show whether discord is configured, but mask the full URL
    url = result.get("discord_webhook_url", "")
    result["discord_active"] = bool(url)
    if url:
        result["discord_webhook_url"] = url[:50] + "..." if len(url) > 50 else url
    return result


@router.put("/config")
async def update_bias_config(request: Request, body: dict):
    store = request.app.state.store
    allowed = {"enabled", "confidence_threshold", "account_id", "on_contradicting_bias", "trail_pct", "discord_webhook_url"}
    fields = {k: v for k, v in body.items() if k in allowed}
    config = await store.update_bias_config(**fields)

    # Live-update discord notifier if URL changed
    if "discord_webhook_url" in fields:
        loop = getattr(request.app.state, "loop", None)
        if loop and hasattr(loop, "discord"):
            loop.discord.update_url(fields["discord_webhook_url"])

    return {k: v for k, v in config.items() if k != "id"}


# -- instruments -------------------------------------------------------------

@router.get("/instruments")
async def get_bias_instruments(request: Request):
    store = request.app.state.store
    instruments = await store.get_bias_instruments()
    return instruments


@router.put("/instruments/{epic}")
async def update_bias_instrument(request: Request, epic: str, body: dict):
    store = request.app.state.store
    allowed = {"enabled", "inverted", "num_contracts", "trail_pct"}
    fields = {k: v for k, v in body.items() if k in allowed}
    result = await store.update_bias_instrument(epic, **fields)
    if result is None:
        return {"error": f"Instrument {epic} not found"}
    return result


# -- positions ---------------------------------------------------------------

@router.get("/positions")
async def get_bias_positions(request: Request):
    store = request.app.state.store
    open_trades = await store.get_open_bias_trades()

    # Enrich with live UPL if bias client available
    loop = getattr(request.app.state, "loop", None)
    bias_client = getattr(loop, "_bias_client", None) if loop else None

    live_map = {}
    if bias_client:
        try:
            live_positions = await bias_client.get_positions()
            live_map = {p.deal_id: p for p in live_positions}
        except Exception:
            pass

    # Check signal expiry for each position
    result = []
    for trade in open_trades:
        entry = {
            "id": trade["id"],
            "epic": trade["epic"],
            "deal_id": trade["deal_id"],
            "direction": trade["direction"],
            "size": trade["size"],
            "entry_price": trade["entry_price"],
            "stop_level": trade.get("stop_level"),
            "profit_level": trade.get("profit_level"),
            "opened_at": trade["opened_at"].isoformat() if trade.get("opened_at") else None,
        }

        live = live_map.get(trade["deal_id"])
        if live:
            entry["upl"] = live.upl

        # Check if bias signal is still valid
        bias = await store.get_latest_bias(epic=trade["epic"])
        entry["signal_active"] = bias is not None
        entry["signal_expired"] = bias is None

        result.append(entry)

    return result


# -- trades ------------------------------------------------------------------

@router.get("/trades")
async def get_bias_trades(request: Request, limit: int = 50):
    store = request.app.state.store
    trades = await store.get_bias_trade_history(limit=limit)
    for t in trades:
        for key in ("opened_at", "closed_at"):
            if t.get(key) and hasattr(t[key], "isoformat"):
                t[key] = t[key].isoformat()
    return trades


@router.get("/trades/stats")
async def get_bias_trade_stats(request: Request):
    store = request.app.state.store
    return await store.get_bias_trade_stats()


# -- audit -------------------------------------------------------------------

@router.get("/audit")
async def get_bias_audit(request: Request, limit: int = 100):
    store = request.app.state.store
    rows = await store.get_bias_audit(limit=limit)
    for r in rows:
        if r.get("ts") and hasattr(r["ts"], "isoformat"):
            r["ts"] = r["ts"].isoformat()
    return rows


# -- account -----------------------------------------------------------------

@router.get("/account")
async def get_bias_account(request: Request):
    """Get bias account balance and margin from Capital.com."""
    loop = getattr(request.app.state, "loop", None)
    bias_client = getattr(loop, "_bias_client", None) if loop else None

    if not bias_client:
        return {"error": "Bias client not initialized"}

    try:
        store = request.app.state.store
        config = await store.get_bias_config()
        target_id = config.get("account_id", "")

        accounts = await bias_client.get_accounts()
        for acc in accounts:
            if acc.account_id == target_id:
                return {
                    "account_id": acc.account_id,
                    "balance": acc.balance,
                    "deposit": acc.deposit,
                    "profit_loss": acc.profit_loss,
                    "available": acc.available,
                }
        return {"error": "Account not found"}
    except Exception as e:
        return {"error": str(e)}
