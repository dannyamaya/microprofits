from __future__ import annotations

from collections import deque

from microprofits.api.models import Candle


class CandleHistory:
    def __init__(self, maxlen: int = 30) -> None:
        self._deque: deque[Candle] = deque(maxlen=maxlen)

    def push(self, candle: Candle) -> bool:
        if self._deque and candle.timestamp <= self._deque[-1].timestamp:
            return False
        self._deque.append(candle)
        return True

    def load(self, candles: list[Candle]) -> None:
        for c in sorted(candles, key=lambda x: x.timestamp):
            self._deque.append(c)

    @property
    def candles(self) -> list[Candle]:
        return list(self._deque)

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self._deque]

    @property
    def last(self) -> Candle | None:
        return self._deque[-1] if self._deque else None

    @property
    def prev(self) -> Candle | None:
        return self._deque[-2] if len(self._deque) >= 2 else None

    def __len__(self) -> int:
        return len(self._deque)
