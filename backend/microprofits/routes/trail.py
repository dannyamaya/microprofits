from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/trail", tags=["trail"])


@router.get("/states")
async def get_trail_states(request: Request):
    """Live in-memory trail state for all tracked positions."""
    loop = request.app.state.loop
    states = loop._pct_trailer.trail_states  # dict[deal_id, TrailState]

    result = []
    for deal_id, st in states.items():
        result.append({
            "deal_id": deal_id,
            "peak_upl": round(st.peak_upl, 2),
            "trail_level": round(st.trail_level, 2),
            "initial_sl": round(st.initial_sl, 2),
            "activated": st.activated,
        })
    return result


@router.get("/history")
async def get_trail_history(
    request: Request,
    deal_id: str | None = Query(None),
    epic: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    """Trail snapshots from DB for post-trade analysis."""
    store = request.app.state.store
    rows = await store.get_trail_history(deal_id=deal_id, epic=epic, limit=limit)
    for r in rows:
        if "ts" in r and r["ts"]:
            r["ts"] = r["ts"].isoformat()
    return rows
