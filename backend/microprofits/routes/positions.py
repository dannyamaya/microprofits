from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["positions"])


@router.get("/positions")
async def get_positions(request: Request):
    loop = request.app.state.loop
    client = request.app.state.client

    try:
        live = await client.get_positions()
    except Exception:
        live = []

    live_map = {p.deal_id: p for p in live}
    result = []

    for tracked in loop.tracker.open_positions:
        live_pos = live_map.get(tracked.deal_id)
        result.append({
            "deal_id": tracked.deal_id,
            "epic": tracked.epic,
            "direction": tracked.direction,
            "size": tracked.size,
            "entry_price": tracked.entry_price,
            "stop_level": tracked.stop_level,
            "profit_level": tracked.profit_level,
            "upl": live_pos.upl if live_pos else 0,
            "opened_at": tracked.opened_at.isoformat(),
        })

    return result
