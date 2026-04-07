"""
Discord Webhook Notifier

Posts unified bias reports to Discord — each message contains the full
analysis (bias, levels, catalyst, risk, trade idea) plus any trade action
taken by the bot. Format matches the BillionBot analysis style.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from loguru import logger


# Instrument display names
DISPLAY_NAMES = {
    "NQ": "NQ (Nasdaq 100)",
    "ES": "ES (S&P 500)",
    "OIL": "OIL (Crude)",
    "CL": "OIL (Crude)",
    "US100": "NQ (Nasdaq 100)",
    "OIL_CRUDE": "OIL (Crude)",
}


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
                logger.warning(f"Discord webhook {resp.status_code}: {resp.text[:300]}")
            else:
                logger.debug("Discord webhook sent successfully")
        except Exception as e:
            logger.error(f"Discord webhook error: {e}")

    # -- Unified bias report ---------------------------------------------------

    async def notify_bias_report(
        self,
        signals: list[dict],
        trade_results: list[dict | None],
        market_context: str = "",
    ) -> None:
        """
        Send one unified message with all instrument analyses + trade actions.
        Matches the BillionBot analysis format the user expects.
        """
        embeds = []

        for sig, trade in zip(signals, trade_results):
            instrument = sig.get("instrument", "?")
            display = DISPLAY_NAMES.get(instrument.upper(), instrument)
            bias = sig.get("bias", "NEUTRAL")
            confidence = sig.get("confidence", 0)
            current_price = sig.get("current_price")
            price_target = sig.get("price_target")
            stop_loss = sig.get("stop_loss_price")
            key_support = sig.get("key_support")
            key_resistance = sig.get("key_resistance")
            catalyst = sig.get("catalyst", "")
            risk = sig.get("risk", "")
            trade_idea = sig.get("trade_idea", "")

            color = 0x22C55E if bias == "BULLISH" else 0xEF4444 if bias == "BEARISH" else 0x71717A
            stars = "\u2b50" * confidence

            # Build description in the screenshot format
            lines = []
            lines.append(f"\u2022 BIAS: **{bias}**")
            lines.append(f"\u2022 CONFIDENCE: **{confidence}/5** {stars}")
            if current_price:
                lines.append(f"\u2022 CURRENT PRICE: ${current_price:,.2f}" if current_price > 500 else f"\u2022 CURRENT PRICE: ${current_price:.3f}")
            if key_support:
                lines.append(f"\u2022 KEY SUPPORT: ${key_support:,.2f}" if key_support > 500 else f"\u2022 KEY SUPPORT: ${key_support:.3f}")
            if key_resistance:
                lines.append(f"\u2022 KEY RESISTANCE: ${key_resistance:,.2f}" if key_resistance > 500 else f"\u2022 KEY RESISTANCE: ${key_resistance:.3f}")
            if catalyst:
                lines.append(f"\u2022 CATALYST: {catalyst}")
            if risk:
                lines.append(f"\u2022 RISK: {risk}")
            if trade_idea:
                lines.append(f"\u2022 TRADE IDEA: {trade_idea}")

            # Add trade action if one was taken
            if trade:
                direction = trade.get("direction", "?")
                price = trade.get("price", 0)
                sl = trade.get("sl")
                tp = trade.get("tp")
                inverted = trade.get("inverted", False)
                size = trade.get("size", 0)
                trail_pct = trade.get("trail_pct")

                lines.append("")
                inv_tag = " \u26a0\ufe0f INVERTED" if inverted else ""
                lines.append(f"\u2705 **TRADE OPENED: {direction} x{size} @ ${price:,.2f}**{inv_tag}")
                if sl and tp:
                    lines.append(f"   SL: ${sl:,.2f} | TP: ${tp:,.2f}")
                elif trail_pct:
                    lines.append(f"   Trail: {trail_pct}%")

            desc = "\n".join(lines)
            # Discord embed description limit is 4096
            if len(desc) > 4000:
                desc = desc[:4000] + "..."

            embed = {
                "title": f"**{display}:**",
                "description": desc,
                "color": color,
            }
            embeds.append(embed)

        # Add market context as a separate embed if present
        if market_context:
            embeds.append({
                "title": "**MARKET CONTEXT:**",
                "description": market_context[:4000],
                "color": 0x2F3136,
            })

        if not embeds:
            return

        # Discord max 10 embeds per message
        embeds = embeds[:10]

        # Add timestamp footer to the last embed
        embeds[-1]["timestamp"] = datetime.now(timezone.utc).isoformat()
        embeds[-1]["footer"] = {"text": "Microprofits Bias Strategy"}

        await self._send(embeds=embeds)

    # -- Trade closed ----------------------------------------------------------

    async def notify_bias_close(self, epic: str, exit_reason: str, pnl: float, deal_id: str) -> None:
        """Post when a bias trade is closed."""
        display = DISPLAY_NAMES.get(epic, epic)
        color = 0x22C55E if pnl >= 0 else 0xEF4444
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        emoji = "\u2705" if pnl >= 0 else "\u274c"

        embed = {
            "title": f"{emoji} Trade Closed: {display}",
            "description": f"**{exit_reason}** | P&L: **{pnl_str}**",
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Microprofits Bias Strategy"},
        }

        await self._send(embeds=[embed])
