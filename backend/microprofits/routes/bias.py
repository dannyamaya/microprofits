from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel

from loguru import logger

from microprofits.strategy.bias_provider import INSTRUMENT_TO_EPIC, BiasSignal

router = APIRouter(prefix="/api", tags=["bias"])


class BiasSignalInput(BaseModel):
    instrument: str           # NQ, ES
    bias: str                 # BULLISH, BEARISH, NEUTRAL
    confidence: int           # 1-5
    current_price: float | None = None
    price_target: float | None = None
    stop_loss_price: float | None = None
    key_support: float | None = None
    key_resistance: float | None = None
    catalyst: str = ""
    risk: str = ""
    trade_idea: str = ""
    market_context: str = ""
    ttl_minutes: int = 240    # bias valid for 4 hours by default


class BiasReportInput(BaseModel):
    """Accept a full bias report with multiple instruments at once."""
    signals: list[BiasSignalInput]
    market_context: str = ""


@router.post("/bias")
async def push_bias(request: Request, body: BiasReportInput):
    """Receive bias signals from BillionBot analysis pipeline."""
    store = request.app.state.store
    saved = []
    discord_signals: list[dict] = []
    discord_trades: list[dict | None] = []

    for sig in body.signals:
        epic = INSTRUMENT_TO_EPIC.get(sig.instrument.upper(), "")
        expires = datetime.now(timezone.utc) + timedelta(minutes=sig.ttl_minutes)
        context = sig.market_context or body.market_context

        signal_id = await store.save_bias_signal(
            instrument=sig.instrument.upper(),
            epic=epic,
            bias=sig.bias.upper(),
            confidence=max(1, min(5, sig.confidence)),
            current_price=sig.current_price,
            price_target=sig.price_target,
            stop_loss_price=sig.stop_loss_price,
            key_support=sig.key_support,
            key_resistance=sig.key_resistance,
            catalyst=sig.catalyst,
            risk=sig.risk,
            trade_idea=sig.trade_idea,
            market_context=context,
            expires_at=expires,
        )
        saved.append({"id": signal_id, "instrument": sig.instrument, "bias": sig.bias})

        await store.log_audit(
            epic or sig.instrument,
            "BIAS_RECEIVED",
            {
                "instrument": sig.instrument,
                "bias": sig.bias,
                "confidence": sig.confidence,
                "price_target": sig.price_target,
                "stop_loss_price": sig.stop_loss_price,
            },
        )

        # Trigger bias strategy trade if enabled
        trade_result = await _try_bias_trade(request, sig, epic, signal_id)
        if trade_result:
            saved[-1]["trade"] = trade_result

        # Collect for unified Discord message
        discord_signals.append({
            "instrument": sig.instrument,
            "bias": sig.bias,
            "confidence": sig.confidence,
            "current_price": sig.current_price,
            "price_target": sig.price_target,
            "stop_loss_price": sig.stop_loss_price,
            "key_support": sig.key_support,
            "key_resistance": sig.key_resistance,
            "catalyst": sig.catalyst,
            "risk": sig.risk,
            "trade_idea": sig.trade_idea,
        })
        discord_trades.append(trade_result)

    # Send one unified Discord message with all signals + trade actions
    await _notify_discord_report(request, discord_signals, discord_trades, body.market_context)

    return {"saved": saved, "count": len(saved)}


async def _try_bias_trade(
    request: Request, sig: BiasSignalInput, epic: str, signal_id: int
) -> dict | None:
    """Attempt to open a bias trade from the incoming signal."""
    loop = getattr(request.app.state, "loop", None)
    if not loop:
        return None

    bias_strategy = getattr(loop, "bias_strategy", None)
    if not bias_strategy:
        return None

    bias_client = getattr(loop, "_bias_client", None)
    if not bias_client:
        return None

    bias_signal = BiasSignal(
        instrument=sig.instrument.upper(),
        epic=epic,
        direction=sig.bias.upper(),
        confidence=max(1, min(5, sig.confidence)),
        current_price=sig.current_price,
        price_target=sig.price_target,
        stop_loss_price=sig.stop_loss_price,
        key_support=sig.key_support,
        key_resistance=sig.key_resistance,
        catalyst=sig.catalyst,
        risk=sig.risk,
        trade_idea=sig.trade_idea,
    )

    try:
        result = await bias_strategy.process_signal(bias_signal, signal_id, bias_client)
        return result
    except Exception as e:
        logger.error(f"Bias strategy error for {epic}: {e}")
        return None


async def _notify_discord_report(
    request: Request,
    signals: list[dict],
    trades: list[dict | None],
    market_context: str = "",
) -> None:
    """Send unified Discord message with all signals + trade actions."""
    loop = getattr(request.app.state, "loop", None)
    discord = getattr(loop, "discord", None) if loop else None
    if not discord or not discord.enabled:
        return
    try:
        await discord.notify_bias_report(signals, trades, market_context)
    except Exception as e:
        logger.error(f"Discord report notification failed: {e}")


@router.post("/bias/signal")
async def push_single_bias(request: Request, body: BiasSignalInput):
    """Convenience endpoint: push a single instrument's bias."""
    report = BiasReportInput(signals=[body])
    return await push_bias(request, report)


@router.get("/bias")
async def get_bias(request: Request):
    """Get the latest bias for all instruments."""
    store = request.app.state.store
    biases = await store.get_all_latest_biases()

    result = {}
    for b in biases:
        instrument = b["instrument"]
        result[instrument] = {
            "bias": b["bias"],
            "confidence": b["confidence"],
            "current_price": b.get("current_price"),
            "price_target": b.get("price_target"),
            "stop_loss_price": b.get("stop_loss_price"),
            "key_support": b.get("key_support"),
            "key_resistance": b.get("key_resistance"),
            "catalyst": b.get("catalyst", ""),
            "risk": b.get("risk", ""),
            "trade_idea": b.get("trade_idea", ""),
            "market_context": b.get("market_context", ""),
            "created_at": b["created_at"].isoformat() if b.get("created_at") else None,
            "expires_at": b["expires_at"].isoformat() if b.get("expires_at") else None,
        }

    return result


@router.get("/bias/live")
async def get_bias_live(request: Request):
    """Get bias with live price distance calculations."""
    store = request.app.state.store
    biases = await store.get_all_latest_biases()

    # Try to get live prices from the trading loop
    loop = getattr(request.app.state, "loop", None)

    result = {}
    for b in biases:
        instrument = b["instrument"]
        epic = b.get("epic", "")

        live_price = None
        if loop and epic and epic in loop._histories:
            history = loop._histories[epic]
            if history.candles:
                live_price = history.candles[-1].close

        entry = {
            "bias": b["bias"],
            "confidence": b["confidence"],
            "price_target": b.get("price_target"),
            "stop_loss_price": b.get("stop_loss_price"),
            "key_support": b.get("key_support"),
            "key_resistance": b.get("key_resistance"),
            "catalyst": b.get("catalyst", ""),
            "created_at": b["created_at"].isoformat() if b.get("created_at") else None,
        }

        if live_price:
            entry["live_price"] = live_price
            if b.get("price_target"):
                entry["distance_to_target"] = round(b["price_target"] - live_price, 2)
                entry["distance_to_target_pct"] = round(
                    (b["price_target"] - live_price) / live_price * 100, 3
                )
            if b.get("stop_loss_price"):
                entry["distance_to_sl"] = round(b["stop_loss_price"] - live_price, 2)
            if b.get("key_support"):
                entry["distance_to_support"] = round(live_price - b["key_support"], 2)
            if b.get("key_resistance"):
                entry["distance_to_resistance"] = round(b["key_resistance"] - live_price, 2)

        result[instrument] = entry

    return result


@router.get("/bias/history")
async def get_bias_history(request: Request, instrument: str | None = None, limit: int = 50):
    """Get historical bias signals."""
    store = request.app.state.store
    return await store.get_bias_history(instrument=instrument, limit=limit)
