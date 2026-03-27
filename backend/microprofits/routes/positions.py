from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["positions"])


@router.get("/positions")
async def get_positions(request: Request):
    loop = request.app.state.loop
    client = request.app.state.client
    store = request.app.state.store

    # Get profit_target for locked profit calculation
    config = await store.get_bot_config()
    profit_target = config.get("profit_target", 5.0)

    try:
        live = await client.get_positions()
    except Exception:
        live = []

    live_map = {p.deal_id: p for p in live}
    result = []

    for tracked in loop.tracker.open_positions:
        live_pos = live_map.get(tracked.deal_id)
        locked_profit = tracked.trail_locks * profit_target
        result.append({
            "deal_id": tracked.deal_id,
            "epic": tracked.epic,
            "direction": tracked.direction,
            "size": tracked.size,
            "entry_price": tracked.entry_price,
            "stop_level": tracked.stop_level,
            "upl": live_pos.upl if live_pos else 0,
            "trail_locks": tracked.trail_locks,
            "locked_profit": locked_profit,
            "breakeven_hit": tracked.breakeven_hit,
            "opened_at": tracked.opened_at.isoformat(),
        })

    return result
