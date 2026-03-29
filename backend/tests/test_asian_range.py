from datetime import datetime, timedelta, timezone

from microprofits.api.models import Candle
from microprofits.strategy.candle_history import CandleHistory
from microprofits.strategy.asian_range import AsianRangeBreakout


def _candle(hour: int, minute: int, close: float, high: float | None = None, low: float | None = None) -> Candle:
    """Create a candle at a specific hour:minute on 2026-01-02 UTC."""
    return Candle(
        timestamp=datetime(2026, 1, 2, hour, minute, tzinfo=timezone.utc),
        open=close - 0.5,
        high=high if high is not None else close + 1,
        low=low if low is not None else close - 1,
        close=close,
    )


def _build_asian_history() -> CandleHistory:
    """Build a history with 420 candles covering 00:00–06:59 UTC (full Asian session).

    Price oscillates between 2000 and 2020, with:
    - High of session: 2021 (at candle highs)
    - Low of session: 1999 (at candle lows)
    - Range width: 22 points
    """
    h = CandleHistory(maxlen=500)
    for i in range(420):  # 7 hours * 60 minutes
        hour = i // 60
        minute = i % 60
        # Oscillate close between 2005 and 2015
        close = 2005 + (i % 20) * 0.5
        candle = Candle(
            timestamp=datetime(2026, 1, 2, hour, minute, tzinfo=timezone.utc),
            open=close - 0.5,
            high=close + 1,  # max high = ~2016
            low=close - 1,   # min low = ~2004
            close=close,
        )
        h.push(candle)
    return h


def _build_narrow_asian_history() -> CandleHistory:
    """Asian session with very narrow range (< 2 points)."""
    h = CandleHistory(maxlen=500)
    for i in range(420):
        hour = i // 60
        minute = i % 60
        close = 2010.0
        candle = Candle(
            timestamp=datetime(2026, 1, 2, hour, minute, tzinfo=timezone.utc),
            open=close,
            high=close + 0.5,
            low=close - 0.5,
            close=close,
        )
        h.push(candle)
    return h


class TestAsianRangeBreakout:
    def setup_method(self):
        self.strategy = AsianRangeBreakout()

    def test_buy_breakout_above_asian_high(self):
        history = _build_asian_history()
        asian_high = max(c.high for c in history.candles)

        # Current candle at 08:00 UTC, price above Asian high
        current = _candle(8, 0, asian_high + 2)

        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is not None
        assert signal.direction == "BUY"
        assert signal.epic == "GOLD"
        assert signal.size == 0.5

    def test_sell_breakout_below_asian_low(self):
        history = _build_asian_history()
        asian_low = min(c.low for c in history.candles)

        # Current candle at 09:00 UTC, price below Asian low
        current = _candle(9, 0, asian_low - 2)

        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is not None
        assert signal.direction == "SELL"

    def test_no_signal_inside_range(self):
        history = _build_asian_history()

        # Price inside the Asian range at 08:30
        current = _candle(8, 30, 2010)

        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is None

    def test_no_signal_before_london_open(self):
        history = _build_asian_history()
        asian_high = max(c.high for c in history.candles)

        # Price above range but at 06:00 UTC (still Asian session)
        current = _candle(6, 30, asian_high + 5)

        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is None

    def test_no_signal_after_breakout_window(self):
        history = _build_asian_history()
        asian_high = max(c.high for c in history.candles)

        # Price above range but at 13:00 UTC (after breakout window)
        current = _candle(13, 0, asian_high + 5)

        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is None

    def test_max_one_trade_per_day(self):
        history = _build_asian_history()
        asian_high = max(c.high for c in history.candles)

        current1 = _candle(8, 0, asian_high + 2)
        signal1 = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current1,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal1 is not None

        # Second attempt same day — should be blocked
        current2 = _candle(9, 0, asian_high + 5)
        signal2 = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current2,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal2 is None

    def test_narrow_range_skipped(self):
        history = _build_narrow_asian_history()

        # Range is < 2 points, should skip
        current = _candle(8, 0, 2015)

        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is None

    def test_not_enough_candles_skips(self):
        """If fewer than 30 Asian candles, no range is established."""
        h = CandleHistory(maxlen=500)
        # Only 10 candles
        for i in range(10):
            h.push(Candle(
                timestamp=datetime(2026, 1, 2, 0, i, tzinfo=timezone.utc),
                open=2009.5, high=2011, low=2009, close=2010,
            ))

        current = _candle(8, 0, 2020)
        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=h,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is None

    def test_tp_is_1_5x_range(self):
        history = _build_asian_history()
        asian_high = max(c.high for c in history.candles)
        asian_low = min(c.low for c in history.candles)
        range_width = asian_high - asian_low

        breakout_price = asian_high + 2
        current = _candle(8, 0, breakout_price)

        signal = self.strategy.check_entry(
            epic="GOLD",
            current_candle=current,
            history=history,
            num_contracts=0.5,
            profit_target=20,
            stop_loss=20,
            min_stop_distance=0.5,
        )
        assert signal is not None
        expected_tp = round(breakout_price + range_width * 1.5, 2)
        assert signal.profit_level == expected_tp
