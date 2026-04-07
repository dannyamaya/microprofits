"""
Discord Interactions Endpoint

Handles Discord slash commands (e.g. /bias) via the Interactions Endpoint URL.
Discord sends HTTP POST requests here when a user invokes a command.
No bot process needed — Discord calls us directly.

Setup:
1. Set Interactions Endpoint URL in Discord Developer Portal to:
   https://<your-domain>/api/discord/interactions
2. Register commands via /api/discord/register (one-time)
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from loguru import logger

from microprofits.strategy.bias_provider import INSTRUMENT_TO_EPIC

DISCORD_APP_ID = "1479466159366606891"
DISCORD_PUBLIC_KEY = "81bb7064ac7b924a6bd04207319022e53555ee9737cb6962864d6f0098279cec"

router = APIRouter(prefix="/api/discord", tags=["discord"])


def _verify_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Discord request signature using Ed25519."""
    try:
        from nacl.signing import VerifyKey
        verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except Exception:
        return False


@router.post("/interactions")
async def discord_interactions(request: Request):
    """Handle Discord interaction webhooks (slash commands)."""
    body = await request.body()
    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")

    if not _verify_signature(body, signature, timestamp):
        return Response(status_code=401, content="Invalid signature")

    data = await request.json()
    interaction_type = data.get("type")

    # Type 1: PING (Discord verification handshake)
    if interaction_type == 1:
        return {"type": 1}

    # Type 2: APPLICATION_COMMAND (slash command)
    if interaction_type == 2:
        command = data.get("data", {}).get("name", "")
        if command == "bias":
            return await _handle_bias_command(request)

    return {"type": 1}


async def _handle_bias_command(request: Request) -> dict:
    """Handle /bias slash command — return current active bias."""
    store = request.app.state.store
    biases = await store.get_all_latest_biases()
    instruments = await store.get_bias_instruments()

    inv_map = {i["epic"]: i.get("inverted", False) for i in instruments}

    if not biases:
        return _ephemeral("No active bias signals.")

    lines = []
    for b in biases:
        instrument = b["instrument"]
        epic = b.get("epic", "")
        bias = b["bias"]
        confidence = b["confidence"]
        stars = "\u2b50" * confidence
        inverted = inv_map.get(epic, False)

        # Resolve trade direction
        trade_dir = ""
        if bias == "BULLISH":
            trade_dir = "SELL" if inverted else "BUY"
        elif bias == "BEARISH":
            trade_dir = "BUY" if inverted else "SELL"

        header = f"**{instrument}** ({epic})"
        if inverted:
            header += " \u26a0\ufe0f INV"

        parts = [header]
        parts.append(f"\u2022 Bias: **{bias}** {stars} ({confidence}/5)")
        if trade_dir:
            parts.append(f"\u2022 Trade: **{trade_dir}**")
        if b.get("current_price"):
            parts.append(f"\u2022 Price: ${b['current_price']:,.2f}" if b['current_price'] > 500 else f"\u2022 Price: ${b['current_price']:.3f}")
        if b.get("price_target"):
            pt = b["price_target"]
            parts.append(f"\u2022 Target: ${pt:,.2f}" if pt > 500 else f"\u2022 Target: ${pt:.3f}")
        if b.get("stop_loss_price"):
            sl = b["stop_loss_price"]
            parts.append(f"\u2022 SL: ${sl:,.2f}" if sl > 500 else f"\u2022 SL: ${sl:.3f}")
        if b.get("catalyst"):
            parts.append(f"\u2022 Catalyst: {b['catalyst'][:200]}")
        if b.get("expires_at"):
            from datetime import datetime, timezone
            exp = b["expires_at"]
            if hasattr(exp, "tzinfo"):
                remaining = (exp - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    h = int(remaining // 3600)
                    m = int((remaining % 3600) // 60)
                    parts.append(f"\u2022 Expires in: {h}h {m}m")
                else:
                    parts.append("\u2022 **EXPIRED**")

        lines.append("\n".join(parts))

    # Check open positions
    open_trades = await store.get_open_bias_trades()
    if open_trades:
        lines.append("\n**Open Positions:**")
        for t in open_trades:
            lines.append(f"\u2022 {t['direction']} {t['epic']} x{t['size']} @ ${t['entry_price']:,.2f}")

    content = "\n\n".join(lines)
    # Discord message limit is 2000 chars
    if len(content) > 1900:
        content = content[:1900] + "\n..."

    return {
        "type": 4,  # CHANNEL_MESSAGE_WITH_SOURCE
        "data": {"content": content},
    }


def _ephemeral(msg: str) -> dict:
    """Return an ephemeral (only visible to caller) Discord response."""
    return {
        "type": 4,
        "data": {"content": msg, "flags": 64},
    }


@router.post("/register")
async def register_commands(request: Request):
    """Register the /bias slash command with Discord. Call once."""
    import httpx

    # Need bot token from settings
    from microprofits.config.settings import settings
    bot_token = getattr(settings, "discord_bot_token", "")
    if not bot_token:
        # Try from bias_config
        store = request.app.state.store
        config = await store.get_bias_config()
        bot_token = config.get("discord_bot_token", "")

    if not bot_token:
        return {"error": "No discord_bot_token configured. Set DISCORD_BOT_TOKEN in .env"}

    url = f"https://discord.com/api/v10/applications/{DISCORD_APP_ID}/commands"
    headers = {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}

    commands = [
        {
            "name": "bias",
            "type": 1,
            "description": "Show the current active bias signals and open positions",
        },
    ]

    async with httpx.AsyncClient() as client:
        results = []
        for cmd in commands:
            resp = await client.post(url, headers=headers, json=cmd)
            results.append({"command": cmd["name"], "status": resp.status_code, "response": resp.json()})

    return {"registered": results}
