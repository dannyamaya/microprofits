from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["heatmap"])


@router.get("/heatmap")
async def get_heatmap(request: Request):
    store = request.app.state.store
    rows = await store.get_heatmap()
    # Convert Decimal types for JSON
    return [
        {
            "day_of_week": int(r["day_of_week"]),
            "hour": int(r["hour"]),
            "trade_count": r["trade_count"],
            "pnl": float(r["pnl"]),
        }
        for r in rows
    ]
