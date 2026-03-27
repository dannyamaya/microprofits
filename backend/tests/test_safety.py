import time
from unittest.mock import patch

from microprofits.engine.safety import SafetyLock


class TestSafetyLock:
    def test_initial_state(self):
        s = SafetyLock(max_consecutive=10, loss_threshold=1.0)
        assert s.consecutive_losses == 0
        assert s.is_locked is False

    def test_loss_below_threshold_ignored(self):
        s = SafetyLock(max_consecutive=3, loss_threshold=1.0)
        s.record_trade(-0.50)  # not a "real" loss
        assert s.consecutive_losses == 0

    def test_loss_at_threshold_counts(self):
        s = SafetyLock(max_consecutive=3, loss_threshold=1.0)
        s.record_trade(-1.0)
        assert s.consecutive_losses == 1

    def test_loss_above_threshold_counts(self):
        s = SafetyLock(max_consecutive=3, loss_threshold=1.0)
        s.record_trade(-5.0)
        assert s.consecutive_losses == 1

    def test_win_resets_counter(self):
        s = SafetyLock(max_consecutive=10, loss_threshold=1.0)
        s.record_trade(-2.0)
        s.record_trade(-3.0)
        assert s.consecutive_losses == 2
        s.record_trade(5.0)  # win
        assert s.consecutive_losses == 0

    def test_breakeven_resets_counter(self):
        s = SafetyLock(max_consecutive=10, loss_threshold=1.0)
        s.record_trade(-2.0)
        s.record_trade(-3.0)
        s.record_trade(0.0)  # breakeven
        assert s.consecutive_losses == 0

    def test_lock_triggers_at_max(self):
        s = SafetyLock(max_consecutive=3, loss_threshold=1.0, cooldown_seconds=3600)
        s.record_trade(-2.0)
        s.record_trade(-2.0)
        assert s.is_locked is False
        s.record_trade(-2.0)  # 3rd loss → lock
        assert s.is_locked is True
        assert s.consecutive_losses == 3

    def test_lock_remaining_seconds(self):
        s = SafetyLock(max_consecutive=2, loss_threshold=1.0, cooldown_seconds=60)
        s.record_trade(-5.0)
        s.record_trade(-5.0)
        assert s.is_locked is True
        remaining = s.lock_remaining_seconds
        assert 55 < remaining <= 60

    def test_lock_auto_expires(self):
        s = SafetyLock(max_consecutive=2, loss_threshold=1.0, cooldown_seconds=1)
        s.record_trade(-5.0)
        s.record_trade(-5.0)
        assert s.is_locked is True
        # Simulate time passing
        s._locked_until = time.time() - 1
        assert s.is_locked is False
        assert s.consecutive_losses == 0  # reset after unlock

    def test_can_still_lose_after_unlock(self):
        s = SafetyLock(max_consecutive=2, loss_threshold=1.0, cooldown_seconds=1)
        s.record_trade(-5.0)
        s.record_trade(-5.0)
        s._locked_until = time.time() - 1  # expire
        assert s.is_locked is False

        # New streak starts fresh
        s.record_trade(-5.0)
        assert s.consecutive_losses == 1
        assert s.is_locked is False

    def test_update_config(self):
        s = SafetyLock(max_consecutive=10, loss_threshold=1.0, cooldown_seconds=3600)
        s.update_config(max_consecutive=5, loss_threshold=2.0, cooldown_seconds=1800)
        assert s.max_consecutive == 5
        assert s.loss_threshold == 2.0
        assert s.cooldown_seconds == 1800

    def test_status_dict(self):
        s = SafetyLock(max_consecutive=10, loss_threshold=1.0, cooldown_seconds=3600)
        s.record_trade(-2.0)
        status = s.status()
        assert status["consecutive_losses"] == 1
        assert status["max_consecutive"] == 10
        assert status["is_locked"] is False
        assert status["loss_threshold"] == 1.0

    def test_mixed_sequence(self):
        s = SafetyLock(max_consecutive=5, loss_threshold=1.0)
        s.record_trade(-2.0)   # 1
        s.record_trade(-3.0)   # 2
        s.record_trade(-0.5)   # below threshold, resets
        assert s.consecutive_losses == 0
        s.record_trade(-1.5)   # 1
        s.record_trade(-1.0)   # 2
        s.record_trade(-2.0)   # 3
        s.record_trade(1.0)    # win, resets
        assert s.consecutive_losses == 0
        assert s.is_locked is False
