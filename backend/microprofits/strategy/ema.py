from __future__ import annotations


def ema(closes: list[float], period: int = 5) -> list[float]:
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(closes[:period]) / period]
    for price in closes[period:]:
        result.append(price * k + result[-1] * (1 - k))
    return result


def ema_slope_positive(closes: list[float], period: int = 5) -> bool:
    values = ema(closes, period)
    if len(values) < 2:
        return False
    return values[-1] > values[-2]
