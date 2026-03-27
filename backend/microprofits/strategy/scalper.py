from __future__ import annotations

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


class MomentumScalper:
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
    ) -> EntrySignal | None:
        prev = history.last
        if prev is None:
            return None

        current_price = current_candle.close

        # Entry condition: current price > previous closed candle close
        if current_price <= prev.close:
            return None

        # Optional EMA slope filter
        if ema_filter_on:
            closes = history.closes
            if not ema_slope_positive(closes, ema_period):
                logger.debug(f"{epic}: EMA slope negative — skipping")
                return None

        # points = dollars / num_contracts
        tp_distance = profit_target / num_contracts
        sl_distance = stop_loss / num_contracts

        if sl_distance < min_stop_distance:
            sl_distance = min_stop_distance
        if tp_distance < min_stop_distance:
            tp_distance = min_stop_distance

        stop_level = round(current_price - sl_distance, 2)
        profit_level = round(current_price + tp_distance, 2)

        return EntrySignal(
            epic=epic,
            direction="BUY",
            size=num_contracts,
            stop_level=stop_level,
            profit_level=profit_level,
            entry_price=current_price,
        )
