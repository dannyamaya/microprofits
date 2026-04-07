from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["headlines"])


@router.get("/headlines")
async def get_headlines(request: Request, hours: int = 24, limit: int = 60):
    """Get recent X headlines."""
    store = request.app.state.store
    headlines = await store.get_recent_headlines(hours=hours, limit=limit)
    return {"headlines": headlines, "count": len(headlines)}


@router.get("/headlines/stats")
async def get_headline_stats(request: Request):
    """Get headline storage stats."""
    store = request.app.state.store
    return await store.get_headline_stats()


@router.post("/headlines/fetch")
async def trigger_fetch(request: Request, backfill: bool = False):
    """Manually trigger a headline fetch from X API."""
    loop = getattr(request.app.state, "loop", None)
    if not loop or not hasattr(loop, "x_scraper"):
        return {"error": "X scraper not configured"}

    scraper = loop.x_scraper
    if not scraper.configured:
        return {"error": "X API bearer token not set"}

    new_count = await scraper.fetch_new_headlines(backfill=backfill)
    stats = await request.app.state.store.get_headline_stats()
    return {
        "new_headlines": new_count,
        "total_in_db": stats.get("total", 0),
        "backfill": backfill,
    }
