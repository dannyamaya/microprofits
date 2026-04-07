from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel

from microprofits.strategy.bias_provider import INSTRUMENT_TO_EPIC

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

    return {"saved": saved, "count": len(saved)}


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
