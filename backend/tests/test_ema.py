from microprofits.strategy.ema import ema, ema_slope_positive


def test_ema_basic():
    closes = [10, 11, 12, 13, 14, 15, 16]
    result = ema(closes, period=5)
    assert len(result) == 3  # 7 - 5 + 1
    assert result[0] == 12.0  # SMA of first 5: (10+11+12+13+14)/5


def test_ema_too_few():
    assert ema([1, 2, 3], period=5) == []


def test_ema_slope_positive_rising():
    closes = [10, 11, 12, 13, 14, 15, 16]
    assert ema_slope_positive(closes, period=5) is True


def test_ema_slope_positive_falling():
    closes = [16, 15, 14, 13, 12, 11, 10]
    assert ema_slope_positive(closes, period=5) is False


def test_ema_slope_insufficient_data():
    assert ema_slope_positive([10, 11], period=5) is False


def test_ema_slope_flat():
    closes = [10, 10, 10, 10, 10, 10, 10]
    assert ema_slope_positive(closes, period=5) is False
