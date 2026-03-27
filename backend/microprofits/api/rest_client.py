from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
from loguru import logger as _base_logger

logger = _base_logger

from microprofits.api.auth import SessionManager
from microprofits.api.exceptions import (
    CapitalAPIError,
    OrderError,
    RateLimitError,
)
from microprofits.api.models import (
    Account,
    Candle,
    Instrument,
    OrderConfirmation,
    Position,
)
from microprofits.config.settings import settings

CONFIRM_RETRIES = 3
CONFIRM_DELAY = 1.0


class RestClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30.0)
        self._session = SessionManager()

    async def start(self) -> None:
        await self._session.authenticate(self._client)

    async def close(self) -> None:
        await self._client.aclose()

    # -- low-level request --------------------------------------------------

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> httpx.Response:
        await self._session.ensure_active(self._client)
        url = f"{settings.base_url}{path}"
        headers = {**self._session.headers, "Content-Type": "application/json"}

        resp = await self._client.request(method, url, headers=headers, json=json)

        if resp.status_code == 401:
            logger.warning("Got 401 — re-authenticating")
            await self._session.authenticate(self._client)
            headers = {**self._session.headers, "Content-Type": "application/json"}
            resp = await self._client.request(method, url, headers=headers, json=json)

        if resp.status_code == 429:
            raise RateLimitError(429, "rate_limited", "Too many requests")

        self._session.touch()
        return resp

    async def _get(self, path: str) -> dict:
        resp = await self._request("GET", path)
        if resp.status_code != 200:
            code = ""
            try:
                code = resp.json().get("errorCode", "")
            except Exception:
                pass
            raise CapitalAPIError(resp.status_code, code)
        return resp.json()

    # -- account -------------------------------------------------------------

    async def get_accounts(self) -> list[Account]:
        data = await self._get("/api/v1/accounts")
        out = []
        for a in data.get("accounts", []):
            bal = a.get("balance", {})
            out.append(
                Account(
                    account_id=a["accountId"],
                    balance=bal.get("balance", 0),
                    deposit=bal.get("deposit", 0),
                    profit_loss=bal.get("profitLoss", 0),
                    available=bal.get("available", 0),
                )
            )
        return out

    # -- instruments ---------------------------------------------------------

    async def get_instrument(self, epic: str) -> Instrument:
        data = await self._get(f"/api/v1/markets/{epic}")
        inst = data.get("instrument", {})
        rules = data.get("dealingRules", {})
        min_stop = rules.get("minStopOrProfitDistance", {}).get("value", 6.0)
        return Instrument(
            epic=inst.get("epic", epic),
            name=inst.get("instrumentName", ""),
            min_deal_size=inst.get("minDealSize", 1),
            max_deal_size=inst.get("maxDealSize", 9999),
            margin_factor=inst.get("marginFactor", 0.05),
            min_stop_distance=min_stop,
            currency=inst.get("currencies", [{}])[0].get("code", "USD")
            if inst.get("currencies")
            else "USD",
        )

    # -- candles -------------------------------------------------------------

    async def get_prices(
        self, epic: str, resolution: str = "MINUTE", max_bars: int = 10
    ) -> list[Candle]:
        data = await self._get(
            f"/api/v1/prices/{epic}?resolution={resolution}&max={max_bars}"
        )
        candles: list[Candle] = []
        for p in data.get("prices", []):
            ts_str = p.get("snapshotTimeUTC", "") or p.get("snapshotTime", "")
            ts = _parse_ts(ts_str)
            ask_open = p.get("openPrice", {}).get("ask", 0)
            ask_high = p.get("highPrice", {}).get("ask", 0)
            ask_low = p.get("lowPrice", {}).get("ask", 0)
            ask_close = p.get("closePrice", {}).get("ask", 0)
            vol = p.get("lastTradedVolume", 0)
            candles.append(
                Candle(
                    timestamp=ts,
                    open=ask_open,
                    high=ask_high,
                    low=ask_low,
                    close=ask_close,
                    volume=vol,
                )
            )
        return candles

    # -- positions -----------------------------------------------------------

    async def get_positions(self) -> list[Position]:
        data = await self._get("/api/v1/positions")
        out: list[Position] = []
        for item in data.get("positions", []):
            pos = item.get("position", {})
            mkt = item.get("market", {})
            out.append(
                Position(
                    deal_id=pos.get("dealId", ""),
                    epic=mkt.get("epic", ""),
                    direction=pos.get("direction", ""),
                    size=pos.get("size", 0),
                    open_level=pos.get("level", 0),
                    stop_level=pos.get("stopLevel"),
                    profit_level=pos.get("profitLevel"),
                    upl=pos.get("upl", 0),
                    currency=pos.get("currency", "USD"),
                )
            )
        return out

    async def open_position(
        self,
        epic: str,
        direction: str,
        size: float,
        stop_level: float | None = None,
        profit_level: float | None = None,
    ) -> OrderConfirmation:
        body: dict = {"epic": epic, "direction": direction, "size": size}
        if stop_level is not None:
            body["stopLevel"] = round(stop_level, 2)
        if profit_level is not None:
            body["profitLevel"] = round(profit_level, 2)
        body["guaranteedStop"] = False

        resp = await self._request("POST", "/api/v1/positions", json=body)
        data = resp.json()
        deal_ref = data.get("dealReference", "")
        if not deal_ref:
            raise OrderError("", "FAILED", f"No dealReference: {data}")
        return await self._confirm_deal(deal_ref)

    async def close_position(
        self, deal_id: str, size: float | None = None
    ) -> OrderConfirmation:
        body: dict = {}
        if size is not None:
            body["size"] = size
        resp = await self._request("DELETE", f"/api/v1/positions/{deal_id}", json=body)
        data = resp.json()
        deal_ref = data.get("dealReference", "")
        if not deal_ref:
            raise OrderError("", "FAILED", f"No dealReference on close: {data}")
        return await self._confirm_deal(deal_ref)

    async def update_position(
        self,
        deal_id: str,
        stop_level: float | None = None,
        profit_level: float | None = None,
    ) -> OrderConfirmation:
        body: dict = {}
        if stop_level is not None:
            body["stopLevel"] = round(stop_level, 2)
        if profit_level is not None:
            body["profitLevel"] = round(profit_level, 2)
        body["guaranteedStop"] = False
        resp = await self._request("PUT", f"/api/v1/positions/{deal_id}", json=body)
        data = resp.json()
        deal_ref = data.get("dealReference", "")
        return await self._confirm_deal(deal_ref)

    async def update_position_fast(
        self,
        deal_id: str,
        stop_level: float,
    ) -> None:
        """Fast SL update — no confirmation wait, but check HTTP status."""
        body = {"stopLevel": round(stop_level, 2), "guaranteedStop": False}
        resp = await self._request("PUT", f"/api/v1/positions/{deal_id}", json=body)
        if resp.status_code != 200:
            body_text = ""
            try:
                body_text = resp.text
            except Exception:
                pass
            raise OrderError("", "FAILED", f"SL update rejected: {resp.status_code} {body_text}")

    # -- confirmation --------------------------------------------------------

    async def _confirm_deal(self, deal_ref: str) -> OrderConfirmation:
        for attempt in range(CONFIRM_RETRIES):
            await asyncio.sleep(CONFIRM_DELAY)
            try:
                data = await self._get(f"/api/v1/confirms/{deal_ref}")
            except CapitalAPIError as e:
                if e.status == 404 and attempt < CONFIRM_RETRIES - 1:
                    continue
                raise

            status = data.get("dealStatus", "")
            reason = data.get("reason", "") or data.get("rejectReason", "") or str(data)
            if status == "REJECTED":
                logger.warning(f"Order REJECTED: {data}")
                raise OrderError(deal_ref, status, reason)

            return OrderConfirmation(
                deal_id=data.get("dealId", ""),
                deal_reference=deal_ref,
                status=status,
                reason=reason,
                level=data.get("level", 0),
                size=data.get("size", 0),
                direction=data.get("direction", ""),
            )

        raise OrderError(deal_ref, "TIMEOUT", "Confirmation not available after retries")


def _parse_ts(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.utcnow()
