from datetime import datetime, timedelta

from microprofits.api.models import Candle
from microprofits.strategy.candle_history import CandleHistory
from microprofits.strategy.scalper import MomentumScalper


def _candle(ts_offset: int, close: float) -> Candle:
    return Candle(
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=ts_offset),
        open=close - 0.5,
        high=close + 1,
        low=close - 1,
        close=close,
    )


def _history(closes: list[float]) -> CandleHistory:
    h = CandleHistory(maxlen=30)
    for i, c in enumerate(closes):
        h.push(_candle(i, c))
    return h


class TestMomentumScalper:
    def setup_method(self):
        self.scalper = MomentumScalper()

    def test_entry_price_above_prev_close(self):
        history = _history([100, 101, 102, 103, 104])
        current = _candle(5, 105)  # above last close (104)
        signal = self.scalper.check_entry(
            epic="US100",
            current_candle=current,
            history=history,
            num_contracts=1,
            profit_target=5,
            stop_loss=10,
            min_stop_distance=0.01,
            ema_filter_on=False,
        )
        assert signal is not None
        assert signal.epic == "US100"
        assert signal.direction == "BUY"
        assert signal.stop_level == 95  # 105 - 10
        assert signal.profit_level == 110  # 105 + 5
        assert signal.entry_price == 105

    def test_no_entry_price_below_prev(self):
        history = _history([100, 101, 102, 103, 104])
        current = _candle(5, 103)  # below last close (104)
        signal = self.scalper.check_entry(
            epic="US100",
            current_candle=current,
            history=history,
            num_contracts=1,
            profit_target=5,
            stop_loss=10,
            min_stop_distance=0.01,
            ema_filter_on=False,
        )
        assert signal is None

    def test_ema_filter_blocks_entry(self):
        # Falling prices → EMA slope negative
        history = _history([110, 109, 108, 107, 106, 105])
        current = _candle(6, 105.5)  # slightly above last close but EMA falling
        signal = self.scalper.check_entry(
            epic="US100",
            current_candle=current,
            history=history,
            num_contracts=1,
            profit_target=5,
            stop_loss=10,
            min_stop_distance=0.01,
            ema_filter_on=True,
            ema_period=5,
        )
        assert signal is None

    def test_ema_filter_allows_entry(self):
        history = _history([100, 101, 102, 103, 104, 105])
        current = _candle(6, 106)
        signal = self.scalper.check_entry(
            epic="US100",
            current_candle=current,
            history=history,
            num_contracts=1,
            profit_target=5,
            stop_loss=10,
            min_stop_distance=0.01,
            ema_filter_on=True,
            ema_period=5,
        )
        assert signal is not None

    def test_min_stop_distance_enforced(self):
        history = _history([100, 101, 102, 103, 104])
        current = _candle(5, 105)
        signal = self.scalper.check_entry(
            epic="US100",
            current_candle=current,
            history=history,
            num_contracts=1,
            profit_target=1,
            stop_loss=0.5,
            min_stop_distance=3.0,  # larger than calculated
            ema_filter_on=False,
        )
        assert signal is not None
        assert signal.stop_level == 102  # 105 - 3 (min_stop_distance)

    def test_two_contracts_halves_distance(self):
        history = _history([100, 101, 102, 103, 104])
        current = _candle(5, 105)
        signal = self.scalper.check_entry(
            epic="US100",
            current_candle=current,
            history=history,
            num_contracts=2,
            profit_target=10,
            stop_loss=10,
            min_stop_distance=0.01,
            ema_filter_on=False,
        )
        assert signal is not None
        assert signal.stop_level == 100  # 105 - (10/2)
        assert signal.profit_level == 110  # 105 + (10/2)
        assert signal.size == 2

    def test_empty_history(self):
        history = CandleHistory(maxlen=30)
        current = _candle(0, 105)
        signal = self.scalper.check_entry(
            epic="US100",
            current_candle=current,
            history=history,
            num_contracts=1,
            profit_target=5,
            stop_loss=10,
            min_stop_distance=0.01,
            ema_filter_on=False,
        )
        assert signal is None
