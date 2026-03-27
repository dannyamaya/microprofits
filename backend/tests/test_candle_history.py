from datetime import datetime, timedelta

from microprofits.api.models import Candle
from microprofits.strategy.candle_history import CandleHistory


def _candle(ts_offset: int, close: float) -> Candle:
    return Candle(
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=ts_offset),
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
    )


def test_push_new_candle():
    h = CandleHistory(maxlen=5)
    assert h.push(_candle(0, 100))
    assert h.push(_candle(1, 101))
    assert len(h) == 2


def test_push_duplicate_rejected():
    h = CandleHistory(maxlen=5)
    h.push(_candle(0, 100))
    assert h.push(_candle(0, 100)) is False
    assert len(h) == 1


def test_push_old_rejected():
    h = CandleHistory(maxlen=5)
    h.push(_candle(1, 101))
    assert h.push(_candle(0, 100)) is False


def test_maxlen_enforced():
    h = CandleHistory(maxlen=3)
    for i in range(5):
        h.push(_candle(i, 100 + i))
    assert len(h) == 3
    assert h.candles[0].close == 102


def test_last_and_prev():
    h = CandleHistory(maxlen=10)
    h.push(_candle(0, 100))
    h.push(_candle(1, 105))
    assert h.last.close == 105
    assert h.prev.close == 100


def test_last_empty():
    h = CandleHistory(maxlen=10)
    assert h.last is None
    assert h.prev is None


def test_closes():
    h = CandleHistory(maxlen=10)
    h.push(_candle(0, 100))
    h.push(_candle(1, 105))
    h.push(_candle(2, 103))
    assert h.closes == [100, 105, 103]


def test_load():
    h = CandleHistory(maxlen=10)
    candles = [_candle(i, 100 + i) for i in range(5)]
    h.load(candles)
    assert len(h) == 5
    assert h.last.close == 104
