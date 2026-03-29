from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from microprofits.api.models import Candle
from microprofits.strategy.candle_history import CandleHistory
from microprofits.strategy.scalper import EntrySignal


# Asian session: 00:00–07:00 UTC (covers Tokyo + early pre-London)
ASIAN_START_HOUR = 0
ASIAN_END_HOUR = 7

# London open: 08:00 UTC — breakout window starts
BREAKOUT_START_HOUR = 8
# Stop looking for breakouts after 12:00 UTC
BREAKOUT_END_HOUR = 12

# Skip entry if Asian range is wider than this (volatile day)
MAX_RANGE_POINTS = 25.0


@dataclass
class AsianRange:
    high: float
    low: float
    date: str  # YYYY-MM-DD to track which day this range belongs to

    @property
    def width(self) -> float:
        return self.high - self.low


class AsianRangeBreakout:
    """Detect breakout of the Asian session high/low after London open."""

    def __init__(self) -> None:
        self._current_range: AsianRange | None = None
        self._traded_today: bool = False
        self._last_trade_date: str = ""

    def check_entry(
        self,
        epic: str,
        current_candle: Candle,
        history: CandleHistory,
        num_contracts: float,
        profit_target: float,
        stop_loss: float,
        min_stop_distance: float,
    ) -> EntrySignal | None:
        now = current_candle.timestamp
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        today = now.strftime("%Y-%m-%d")

        # Reset traded flag for new day
        if today != self._last_trade_date:
            self._traded_today = False

        # Max 1 trade per day
        if self._traded_today:
            return None

        # Build Asian range from history candles (00:00–07:00 UTC)
        self._build_range(history, today)

        if self._current_range is None or self._current_range.date != today:
            return None

        rng = self._current_range

        # Skip if range is too wide (volatile day)
        if rng.width > MAX_RANGE_POINTS:
            logger.debug(f"{epic}: Asian range {rng.width:.1f} > {MAX_RANGE_POINTS} — skip")
            return None

        # Skip if range is too narrow (no clear structure)
        if rng.width < 2.0:
            logger.debug(f"{epic}: Asian range {rng.width:.1f} too narrow — skip")
            return None

        # Only trade during breakout window (08:00–12:00 UTC)
        hour = now.hour
        if hour < BREAKOUT_START_HOUR or hour >= BREAKOUT_END_HOUR:
            return None

        price = current_candle.close

        # TP = 1.5x range width, SL = opposite end of range
        direction = None
        sl_level = None

        if price > rng.high:
            direction = "BUY"
            sl_distance = price - rng.low
            tp_distance = rng.width * 1.5
        elif price < rng.low:
            direction = "SELL"
            sl_distance = rng.high - price
            tp_distance = rng.width * 1.5
        else:
            return None

        # Enforce min stop distance
        if sl_distance < min_stop_distance:
            sl_distance = min_stop_distance
        if tp_distance < min_stop_distance:
            tp_distance = min_stop_distance

        # Cap risk: if SL distance in dollars exceeds stop_loss config, skip
        sl_dollars = sl_distance * num_contracts
        if sl_dollars > stop_loss * 2:
            logger.debug(f"{epic}: SL ${sl_dollars:.2f} too large — skip")
            return None

        if direction == "BUY":
            sl_level = round(price - sl_distance, 2)
            tp_level = round(price + tp_distance, 2)
        else:
            sl_level = round(price + sl_distance, 2)
            tp_level = round(price - tp_distance, 2)

        self._traded_today = True
        self._last_trade_date = today

        logger.info(
            f"{epic}: ASIAN BREAKOUT {direction} @ {price:.2f} "
            f"range=[{rng.low:.2f}–{rng.high:.2f}] width={rng.width:.1f} "
            f"SL={sl_level:.2f} TP={tp_level:.2f}"
        )

        return EntrySignal(
            epic=epic,
            direction=direction,
            size=num_contracts,
            stop_level=sl_level,
            profit_level=tp_level,
            entry_price=price,
        )

    def _build_range(self, history: CandleHistory, today: str) -> None:
        """Scan history for Asian session candles (00:00–07:00 UTC) of today."""
        if self._current_range and self._current_range.date == today:
            return  # Already built for today

        asian_candles: list[Candle] = []
        for c in history.candles:
            ts = c.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.strftime("%Y-%m-%d") != today:
                continue
            if ASIAN_START_HOUR <= ts.hour < ASIAN_END_HOUR:
                asian_candles.append(c)

        if len(asian_candles) < 30:
            # Need at least ~30 candles (half the session) to establish range
            return

        high = max(c.high for c in asian_candles)
        low = min(c.low for c in asian_candles)

        self._current_range = AsianRange(high=high, low=low, date=today)
        logger.info(
            f"Asian range for {today}: [{low:.2f}–{high:.2f}] "
            f"width={high - low:.1f}"
        )
