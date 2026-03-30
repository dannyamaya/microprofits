from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from loguru import logger

from microprofits.api.models import Candle
from microprofits.strategy.candle_history import CandleHistory
from microprofits.strategy.ema import ema_slope_positive


@dataclass
class EntrySignal:
    epic: str
    direction: str
    size: float
    stop_level: float
    profit_level: float
    entry_price: float


@dataclass
class _PriceTick:
    price: float
    ts: float


class MomentumScalper:
    def __init__(self) -> None:
        # Per-epic price tick buffer for velocity calculation
        self._ticks: dict[str, list[_PriceTick]] = defaultdict(list)

    def check_entry(
        self,
        epic: str,
        current_candle: Candle,
        history: CandleHistory,
        num_contracts: float,
        profit_target: float,
        stop_loss: float,
        min_stop_distance: float,
        ema_filter_on: bool,
        ema_period: int = 5,
        velocity_threshold: float = 0.15,
        velocity_window: int = 3,
        manual_mode: bool = False,
    ) -> EntrySignal | None:
        current_price = current_candle.close
        now = time.time()

        # Record this tick
        ticks = self._ticks[epic]
        ticks.append(_PriceTick(price=current_price, ts=now))

        # Keep only ticks from the last 30s to avoid unbounded growth
        cutoff = now - 30.0
        self._ticks[epic] = [t for t in ticks if t.ts >= cutoff]
        ticks = self._ticks[epic]

        # Need at least velocity_window ticks to measure speed
        if len(ticks) < velocity_window:
            return None

        # Calculate velocity: price change / time elapsed over the last N ticks
        recent = ticks[-velocity_window:]
        price_delta = recent[-1].price - recent[0].price
        time_delta = recent[-1].ts - recent[0].ts

        if time_delta <= 0:
            return None

        velocity = price_delta / time_delta  # points per second

        # Must be moving UP fast enough
        if velocity < velocity_threshold:
            return None

        # Optional EMA slope filter
        if ema_filter_on:
            closes = history.closes
            if not ema_slope_positive(closes, ema_period):
                logger.debug(f"{epic}: velocity OK ({velocity:.3f} pts/s) but EMA slope negative — skipping")
                return None

        logger.info(
            f"{epic}: VELOCITY ENTRY — {velocity:.3f} pts/s "
            f"(threshold={velocity_threshold}) over {time_delta:.1f}s, "
            f"price delta={price_delta:.2f}"
        )

        # Clear ticks after entry to avoid immediate re-entry
        self._ticks[epic].clear()

        # Calculate SL/TP levels
        tp_distance = profit_target / num_contracts
        sl_distance = stop_loss / num_contracts

        if sl_distance < min_stop_distance:
            sl_distance = min_stop_distance
        if tp_distance < min_stop_distance:
            tp_distance = min_stop_distance

        stop_level = round(current_price - sl_distance, 2)
        # No TP on scalper — PctTrailer manages the exit via trailing stop
        profit_level = None

        return EntrySignal(
            epic=epic,
            direction="BUY",
            size=num_contracts,
            stop_level=stop_level,
            profit_level=profit_level,
            entry_price=current_price,
        )
