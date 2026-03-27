import time
from unittest.mock import patch

from microprofits.strategy.velocity import VelocityTracker


class TestVelocityTracker:
    def test_no_data(self):
        v = VelocityTracker(window_seconds=30)
        assert v.get_velocity("US100") == 0.0

    def test_single_tick(self):
        v = VelocityTracker(window_seconds=30)
        v.record("US100", 100)
        assert v.get_velocity("US100") == 0.0  # need at least 2

    def test_positive_velocity(self):
        v = VelocityTracker(window_seconds=30)
        now = time.time()
        # Simulate ticks 10 seconds apart, price rising
        v._ticks["US100"] = []
        from microprofits.strategy.velocity import PriceTick
        v._ticks["US100"].append(PriceTick(timestamp=now - 10, price=100))
        v._ticks["US100"].append(PriceTick(timestamp=now, price=110))
        vel = v.get_velocity("US100")
        assert abs(vel - 1.0) < 0.1  # 10 pts / 10 sec = 1.0

    def test_negative_velocity(self):
        v = VelocityTracker(window_seconds=30)
        now = time.time()
        from microprofits.strategy.velocity import PriceTick
        from collections import deque
        v._ticks["US100"] = deque(maxlen=200)
        v._ticks["US100"].append(PriceTick(timestamp=now - 10, price=110))
        v._ticks["US100"].append(PriceTick(timestamp=now, price=100))
        vel = v.get_velocity("US100")
        assert vel < 0

    def test_is_fast_enough(self):
        v = VelocityTracker(window_seconds=30)
        now = time.time()
        from microprofits.strategy.velocity import PriceTick
        from collections import deque
        v._ticks["US100"] = deque(maxlen=200)
        v._ticks["US100"].append(PriceTick(timestamp=now - 10, price=100))
        v._ticks["US100"].append(PriceTick(timestamp=now, price=105))
        assert v.is_fast_enough("US100", 0.3) is True
        assert v.is_fast_enough("US100", 1.0) is False

    def test_separate_epics(self):
        v = VelocityTracker(window_seconds=30)
        v.record("US100", 100)
        v.record("NVDA", 200)
        assert v.get_velocity("US100") == 0.0
        assert v.get_velocity("NVDA") == 0.0
        assert v.get_velocity("AAPL") == 0.0  # never recorded
