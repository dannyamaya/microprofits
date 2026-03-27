from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class PriceTick:
    timestamp: float  # time.time()
    price: float


class VelocityTracker:
    """Tracks price velocity (points/second) over a rolling window."""

    def __init__(self, window_seconds: float = 30.0) -> None:
        self._window = window_seconds
        self._ticks: dict[str, deque[PriceTick]] = {}  # epic -> deque

    def record(self, epic: str, price: float) -> None:
        if epic not in self._ticks:
            self._ticks[epic] = deque(maxlen=200)
        now = time.time()
        self._ticks[epic].append(PriceTick(timestamp=now, price=price))
        # Prune old ticks beyond 2x window
        cutoff = now - self._window * 2
        while self._ticks[epic] and self._ticks[epic][0].timestamp < cutoff:
            self._ticks[epic].popleft()

    def get_velocity(self, epic: str) -> float:
        """Returns price velocity in points/second over the window.
        Positive = price going up. Zero if not enough data."""
        ticks = self._ticks.get(epic)
        if not ticks or len(ticks) < 2:
            return 0.0

        now = time.time()
        cutoff = now - self._window

        # Find the oldest tick within the window
        oldest = None
        for t in ticks:
            if t.timestamp >= cutoff:
                oldest = t
                break

        if oldest is None or oldest is ticks[-1]:
            return 0.0

        latest = ticks[-1]
        elapsed = latest.timestamp - oldest.timestamp
        if elapsed < 1.0:
            return 0.0

        return (latest.price - oldest.price) / elapsed

    def is_fast_enough(self, epic: str, threshold: float) -> bool:
        """True if current velocity exceeds the threshold (pts/sec)."""
        return self.get_velocity(epic) >= threshold
