from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades")
async def get_trades(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    epic: str | None = None,
):
    store = request.app.state.store
    trades = await store.get_trade_history(limit=limit, offset=offset, epic=epic)
    # Convert datetimes for JSON serialization
    for t in trades:
        for key in ("opened_at", "closed_at"):
            if t.get(key):
                t[key] = t[key].isoformat()
    return trades


@router.get("/trades/stats")
async def get_stats(request: Request, epic: str | None = None):
    store = request.app.state.store
    return await store.get_trade_stats(epic=epic)


@router.get("/audit")
async def get_audit(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    epic: str | None = None,
):
    store = request.app.state.store
    logs = await store.get_audit_log(limit=limit, epic=epic)
    for entry in logs:
        if entry.get("ts"):
            entry["ts"] = entry["ts"].isoformat()
    return logs
