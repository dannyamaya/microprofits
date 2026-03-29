from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["status"])

_start_time: datetime = datetime.utcnow()


@router.get("/status")
async def get_status(request: Request):
    loop = request.app.state.loop
    store = request.app.state.store
    client = request.app.state.client

    config = await store.get_bot_config()

    # Get account balance — find the active account by ID
    balance_info = {}
    try:
        from microprofits.config.settings import settings
        accounts = await client.get_accounts()
        target_id = settings.capital_account_id
        for a in accounts:
            if a.account_id == target_id or (not target_id and a.balance > 0):
                balance_info = {
                    "balance": a.balance,
                    "available": a.available,
                    "profit_loss": a.profit_loss,
                }
                break
    except Exception:
        pass

    return {
        "bot_enabled": config.get("enabled", False),
        "loop_running": loop.is_running,
        "uptime_seconds": (datetime.utcnow() - _start_time).total_seconds(),
        "open_positions": len(loop.tracker.open_positions) + sum(
            len(t.open_positions) for t in loop._trackers.values()
        ),
        "account": balance_info,
    }


@router.post("/bot/start")
async def start_bot(request: Request):
    store = request.app.state.store
    await store.update_bot_config(enabled=True)
    return {"status": "enabled"}


@router.post("/bot/stop")
async def stop_bot(request: Request):
    store = request.app.state.store
    await store.update_bot_config(enabled=False)
    return {"status": "disabled"}


@router.post("/bot/flatten")
async def flatten_all(request: Request):
    loop = request.app.state.loop
    store = request.app.state.store

    # Disable bot
    await store.update_bot_config(enabled=False)

    # Close all tracked positions (default tracker + per-account trackers)
    closed = 0
    all_trackers = [(loop.tracker, loop.client)]
    for acct_id, tracker in loop._trackers.items():
        all_trackers.append((tracker, loop._clients[acct_id]))

    for tracker, client in all_trackers:
        for pos in list(tracker.open_positions):
            try:
                await client.close_position(pos.deal_id)
                await store.save_trade_close(
                    deal_id=pos.deal_id,
                    exit_price=pos.entry_price,
                    pnl=0,
                    exit_reason="FLATTEN",
                )
                await store.log_audit(pos.epic, "FLATTEN", {"deal_id": pos.deal_id})
                tracker._positions.pop(pos.deal_id, None)
                closed += 1
            except Exception:
                pass

    return {"status": "flattened", "positions_closed": closed}
