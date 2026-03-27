from __future__ import annotations

import time

import httpx
from loguru import logger

from microprofits.api.exceptions import AuthenticationError
from microprofits.api.models import Session
from microprofits.config.settings import settings

REFRESH_BEFORE_EXPIRY = 480  # 8 minutes — refresh before 10-min expiry


class SessionManager:
    def __init__(self) -> None:
        self._session: Session | None = None
        self._last_used: float = 0.0

    @property
    def is_expired(self) -> bool:
        if self._session is None:
            return True
        return (time.time() - self._last_used) > REFRESH_BEFORE_EXPIRY

    @property
    def headers(self) -> dict[str, str]:
        if self._session is None:
            return {}
        return {
            "CST": self._session.cst,
            "X-SECURITY-TOKEN": self._session.security_token,
        }

    async def authenticate(self, client: httpx.AsyncClient) -> Session:
        logger.info("Authenticating with Capital.com...")
        resp = await client.post(
            f"{settings.base_url}/api/v1/session",
            headers={
                "X-CAP-API-KEY": settings.capital_api_key,
                "Content-Type": "application/json",
            },
            json={
                "identifier": settings.capital_email,
                "password": settings.capital_password,
                "encryptedPassword": False,
            },
        )
        if resp.status_code != 200:
            code = ""
            try:
                code = resp.json().get("errorCode", "")
            except Exception:
                pass
            raise AuthenticationError(resp.status_code, code, "Login failed")

        cst = resp.headers.get("CST", "")
        sec = resp.headers.get("X-SECURITY-TOKEN", "")
        if not cst or not sec:
            raise AuthenticationError(resp.status_code, "missing_tokens", "No CST/X-SECURITY-TOKEN in response")

        self._session = Session(cst=cst, security_token=sec)
        self._last_used = time.time()
        logger.info("Authenticated successfully")

        # Switch account if specified
        if settings.capital_account_id:
            await self._switch_account(client)

        return self._session

    async def _switch_account(self, client: httpx.AsyncClient) -> None:
        resp = await client.put(
            f"{settings.base_url}/api/v1/session",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"accountId": settings.capital_account_id},
        )
        if resp.status_code == 200:
            logger.info(f"Switched to account {settings.capital_account_id}")
        else:
            logger.warning(f"Failed to switch account: {resp.status_code}")

    async def ensure_active(self, client: httpx.AsyncClient) -> None:
        if self.is_expired:
            await self.authenticate(client)
        self._last_used = time.time()

    def touch(self) -> None:
        self._last_used = time.time()
