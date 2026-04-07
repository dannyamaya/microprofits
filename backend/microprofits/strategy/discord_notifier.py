"""
Discord Webhook Notifier

Posts bias signals, trade executions, and headlines to a Discord channel
via webhook. All posts are fire-and-forget (failures are logged, not raised).
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from loguru import logger


class DiscordNotifier:
    def __init__(self, webhook_url: str = "") -> None:
        self._url = webhook_url
        self._client = httpx.AsyncClient(timeout=10.0)

    def update_url(self, url: str) -> None:
        self._url = url

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    async def close(self) -> None:
        await self._client.aclose()

    async def _send(self, content: str = "", embeds: list[dict] | None = None) -> None:
        if not self._url:
            return
        body: dict = {}
        if content:
            body["content"] = content
        if embeds:
            body["embeds"] = embeds
        try:
            resp = await self._client.post(self._url, json=body)
            if resp.status_code not in (200, 204):
                logger.warning(f"Discord webhook returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"Discord webhook error: {e}")

    # -- Bias signal received --------------------------------------------------

    async def notify_bias_signal(self, signal: dict) -> None:
        """Post when a new bias signal arrives from BillionBot."""
        instrument = signal.get("instrument", "?")
        bias = signal.get("bias", "?")
        confidence = signal.get("confidence", 0)
        price_target = signal.get("price_target")
        stop_loss = signal.get("stop_loss_price")
        catalyst = signal.get("catalyst", "")
        risk = signal.get("risk", "")
        trade_idea = signal.get("trade_idea", "")

        color = 0x22C55E if bias == "BULLISH" else 0xEF4444 if bias == "BEARISH" else 0x71717A

        stars = "\u2605" * confidence + "\u2606" * (5 - confidence)

        fields = [
            {"name": "Direction", "value": f"**{bias}**", "inline": True},
            {"name": "Confidence", "value": f"{stars} ({confidence}/5)", "inline": True},
        ]

        if price_target:
            fields.append({"name": "Price Target", "value": f"`{price_target}`", "inline": True})
        if stop_loss:
            fields.append({"name": "Stop Loss", "value": f"`{stop_loss}`", "inline": True})
        if catalyst:
            fields.append({"name": "Catalyst", "value": catalyst[:1024], "inline": False})
        if risk:
            fields.append({"name": "Risk", "value": risk[:1024], "inline": False})
        if trade_idea:
            fields.append({"name": "Trade Idea", "value": trade_idea[:1024], "inline": False})

        embed = {
            "title": f"Bias Signal: {instrument}",
            "color": color,
            "fields": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Microprofits Bias Strategy"},
        }

        await self._send(embeds=[embed])

    # -- Trade executed --------------------------------------------------------

    async def notify_bias_trade(self, trade: dict) -> None:
        """Post when a bias trade is opened."""
        epic = trade.get("epic", "?")
        direction = trade.get("direction", "?")
        size = trade.get("size", 0)
        price = trade.get("price", 0)
        sl = trade.get("sl")
        tp = trade.get("tp")
        inverted = trade.get("inverted", False)
        trail_pct = trade.get("trail_pct")
        bias = trade.get("bias", "?")
        confidence = trade.get("confidence", 0)

        color = 0x3B82F6  # blue for trades

        exit_info = f"TP: `{tp}` | SL: `{sl}`" if tp and sl else f"Trail: `{trail_pct}%`"
        inversion_note = " (INVERTED)" if inverted else ""

        embed = {
            "title": f"Trade Opened: {direction} {epic}",
            "color": color,
            "fields": [
                {"name": "Direction", "value": f"**{direction}**{inversion_note}", "inline": True},
                {"name": "Size", "value": f"`{size}`", "inline": True},
                {"name": "Entry Price", "value": f"`{price}`", "inline": True},
                {"name": "Exit Strategy", "value": exit_info, "inline": False},
                {"name": "Bias", "value": f"{bias} ({confidence}/5)", "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Microprofits Bias Strategy"},
        }

        await self._send(embeds=[embed])

    # -- Trade closed ----------------------------------------------------------

    async def notify_bias_close(self, epic: str, exit_reason: str, pnl: float, deal_id: str) -> None:
        """Post when a bias trade is closed."""
        color = 0x22C55E if pnl >= 0 else 0xEF4444
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

        embed = {
            "title": f"Trade Closed: {epic} — {exit_reason}",
            "color": color,
            "fields": [
                {"name": "P&L", "value": f"**{pnl_str}**", "inline": True},
                {"name": "Reason", "value": exit_reason, "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Microprofits Bias Strategy"},
        }

        await self._send(embeds=[embed])

    # -- Blocked entry ---------------------------------------------------------

    async def notify_bias_blocked(self, epic: str, reason: str, detail: str = "") -> None:
        """Post when a bias entry is blocked (low confidence, margin, etc.)."""
        embed = {
            "title": f"Entry Blocked: {epic}",
            "color": 0xEAB308,  # yellow
            "fields": [
                {"name": "Reason", "value": reason, "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Microprofits Bias Strategy"},
        }
        if detail:
            embed["fields"].append({"name": "Detail", "value": detail[:1024], "inline": False})

        await self._send(embeds=[embed])
