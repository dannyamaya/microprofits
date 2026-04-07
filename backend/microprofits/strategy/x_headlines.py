"""
X/Twitter Headlines Scraper

Fetches breaking news headlines from @DeItaone (Walter Bloomberg) via X API v2,
stores them in PostgreSQL, and provides them for bias analysis.

Uses since_id tracking to only fetch new tweets — never re-reads old ones.
Cleanup removes tweets older than 3 days to keep the table lean.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import httpx
from loguru import logger


X_API_BASE = "https://api.x.com/2"

# Accounts to follow for market-moving headlines
ACCOUNTS: dict[str, dict] = {
    "DeItaone": {"id": "2704294333", "name": "Walter Bloomberg"},
}


class XHeadlinesScraper:
    def __init__(self, store, bearer_token: str = "") -> None:
        self._store = store
        self._bearer_token = unquote(bearer_token) if bearer_token else ""

    @property
    def configured(self) -> bool:
        return bool(self._bearer_token)

    def update_token(self, token: str) -> None:
        self._bearer_token = unquote(token) if token else ""

    async def fetch_new_headlines(self, backfill: bool = False) -> int:
        """Fetch new headlines from all tracked accounts. Returns total new count."""
        if not self._bearer_token:
            logger.warning("X API bearer token not configured — skipping headline fetch")
            return 0

        total_new = 0
        for username, info in ACCOUNTS.items():
            try:
                if backfill:
                    new = await self._backfill_account(username, info["id"])
                else:
                    new = await self._fetch_account(username, info["id"])
                total_new += new
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning(f"X API rate limited for @{username}")
                else:
                    logger.error(f"X API error for @{username}: {e}")
            except Exception as e:
                logger.error(f"Failed to fetch @{username}: {e}")

        # Cleanup old headlines
        await self._store.cleanup_old_headlines(days=3)

        return total_new

    async def _fetch_account(self, username: str, user_id: str) -> int:
        """Fetch new tweets since our last known tweet for this account."""
        since_id = await self._store.get_latest_headline_id(username)

        params = {
            "max_results": 100,
            "tweet.fields": "created_at,public_metrics,text",
        }
        if since_id:
            params["since_id"] = since_id

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{X_API_BASE}/users/{user_id}/tweets",
                headers={"Authorization": f"Bearer {self._bearer_token}"},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        remaining = resp.headers.get("x-rate-limit-remaining", "?")
        tweets = data.get("data", [])

        new_count = await self._store.store_headlines(tweets, username)
        logger.info(
            f"@{username}: {len(tweets)} fetched, {new_count} new "
            f"(API reads remaining: {remaining})"
        )
        return new_count

    async def _backfill_account(self, username: str, user_id: str) -> int:
        """Paginate through history to backfill headlines."""
        total_new = 0
        pagination_token = None

        for page in range(10):  # max 10 pages ≈ 1000 tweets
            params = {
                "max_results": 100,
                "tweet.fields": "created_at,public_metrics,text",
            }
            if pagination_token:
                params["pagination_token"] = pagination_token

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{X_API_BASE}/users/{user_id}/tweets",
                    headers={"Authorization": f"Bearer {self._bearer_token}"},
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            tweets = data.get("data", [])
            if not tweets:
                break

            new_count = await self._store.store_headlines(tweets, username)
            total_new += new_count
            logger.info(f"@{username} backfill page {page + 1}: {len(tweets)} fetched, {new_count} new")

            pagination_token = data.get("meta", {}).get("next_token")
            if not pagination_token:
                break

        return total_new

    async def get_recent_headlines(self, hours: int = 24, limit: int = 60) -> list[dict]:
        """Get recent headlines for analysis."""
        return await self._store.get_recent_headlines(hours=hours, limit=limit)
