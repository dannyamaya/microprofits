"""
BillionBot Bias Provider

Reads sentiment bias signals from the bias_signals database table
and provides them as entry filters for trading strategies.

BillionBot pushes bias signals via POST /api/bias, and the trading
loop reads them here to gate entries and adjust SL/TP targets.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

# Map BillionBot instrument names to Capital.com epics
INSTRUMENT_TO_EPIC: dict[str, str] = {
    "NQ": "US100",
    "ES": "US500",
    "OIL": "OIL_CRUDE",
    "CL": "OIL_CRUDE",
    "OIL_CRUDE": "OIL_CRUDE",
    "US100": "US100",
}
EPIC_TO_INSTRUMENT: dict[str, str] = {v: k for k, v in INSTRUMENT_TO_EPIC.items()}


@dataclass
class BiasSignal:
    instrument: str       # BillionBot name (NQ, ES)
    epic: str             # Capital.com epic (US100, US500)
    direction: str        # BULLISH, BEARISH, NEUTRAL
    confidence: int       # 1-5
    current_price: float | None = None
    price_target: float | None = None
    stop_loss_price: float | None = None
    key_support: float | None = None
    key_resistance: float | None = None
    catalyst: str = ""
    risk: str = ""
    trade_idea: str = ""

    @property
    def is_bullish(self) -> bool:
        return self.direction == "BULLISH"

    @property
    def is_bearish(self) -> bool:
        return self.direction == "BEARISH"

    def blocks_entry(self, entry_direction: str, min_confidence: int = 3) -> bool:
        """Return True if bias contradicts the entry direction with enough confidence."""
        if entry_direction == "BUY" and self.is_bearish and self.confidence >= min_confidence:
            return True
        if entry_direction == "SELL" and self.is_bullish and self.confidence >= min_confidence:
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "epic": self.epic,
            "direction": self.direction,
            "confidence": self.confidence,
            "current_price": self.current_price,
            "price_target": self.price_target,
            "stop_loss_price": self.stop_loss_price,
            "key_support": self.key_support,
            "key_resistance": self.key_resistance,
            "catalyst": self.catalyst,
            "risk": self.risk,
            "trade_idea": self.trade_idea,
        }


class BiasProvider:
    """Reads bias signals from the database (populated by BillionBot via API)."""

    def __init__(self, store) -> None:
        self._store = store

    async def get_bias(self, epic: str) -> BiasSignal | None:
        """Get the current bias signal for a Capital.com epic (e.g. US100)."""
        row = await self._store.get_latest_bias(epic=epic)
        if not row:
            # Try by instrument name
            instrument = EPIC_TO_INSTRUMENT.get(epic)
            if instrument:
                row = await self._store.get_latest_bias(instrument=instrument)
        if not row:
            return None

        return BiasSignal(
            instrument=row["instrument"],
            epic=row.get("epic", epic),
            direction=row["bias"],
            confidence=row["confidence"],
            current_price=row.get("current_price"),
            price_target=row.get("price_target"),
            stop_loss_price=row.get("stop_loss_price"),
            key_support=row.get("key_support"),
            key_resistance=row.get("key_resistance"),
            catalyst=row.get("catalyst", ""),
            risk=row.get("risk", ""),
            trade_idea=row.get("trade_idea", ""),
        )

    async def get_all(self) -> list[BiasSignal]:
        """Get all current bias signals."""
        rows = await self._store.get_all_latest_biases()
        signals = []
        for row in rows:
            signals.append(BiasSignal(
                instrument=row["instrument"],
                epic=row.get("epic", ""),
                direction=row["bias"],
                confidence=row["confidence"],
                current_price=row.get("current_price"),
                price_target=row.get("price_target"),
                stop_loss_price=row.get("stop_loss_price"),
                key_support=row.get("key_support"),
                key_resistance=row.get("key_resistance"),
                catalyst=row.get("catalyst", ""),
                risk=row.get("risk", ""),
                trade_idea=row.get("trade_idea", ""),
            ))
        return signals
