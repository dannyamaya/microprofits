from __future__ import annotations

import time

from loguru import logger


class SafetyLock:
    """Tracks consecutive losses and triggers a cooldown when threshold is hit.

    A loss counts only if P&L <= -loss_threshold (e.g., -$1).
    Breakeven exits and tiny spread losses are ignored.
    """

    def __init__(
        self,
        max_consecutive: int = 10,
        loss_threshold: float = 1.0,
        cooldown_seconds: float = 3600.0,
    ) -> None:
        self.max_consecutive = max_consecutive
        self.loss_threshold = loss_threshold
        self.cooldown_seconds = cooldown_seconds

        self._consecutive_losses: int = 0
        self._locked_until: float = 0.0

    @property
    def is_locked(self) -> bool:
        if self._locked_until == 0:
            return False
        if time.time() >= self._locked_until:
            # Cooldown expired — auto-resume
            self._unlock()
            return False
        return True

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def lock_remaining_seconds(self) -> float:
        if not self.is_locked:
            return 0
        return max(0, self._locked_until - time.time())

    def record_trade(self, pnl: float) -> None:
        """Call after every closed trade. Triggers lock if threshold hit."""
        if pnl <= -self.loss_threshold:
            self._consecutive_losses += 1
            logger.info(
                f"Safety: consecutive loss #{self._consecutive_losses} "
                f"(pnl=${pnl:.2f}, threshold=-${self.loss_threshold})"
            )
            if self._consecutive_losses >= self.max_consecutive:
                self._lock()
        else:
            # Win or breakeven — reset counter
            if self._consecutive_losses > 0:
                logger.info(
                    f"Safety: streak broken after {self._consecutive_losses} losses "
                    f"(pnl=${pnl:.2f})"
                )
            self._consecutive_losses = 0

    def _lock(self) -> None:
        self._locked_until = time.time() + self.cooldown_seconds
        minutes = self.cooldown_seconds / 60
        logger.warning(
            f"SAFETY LOCK: {self._consecutive_losses} consecutive losses >= "
            f"-${self.loss_threshold}. Bot locked for {minutes:.0f} minutes."
        )

    def _unlock(self) -> None:
        logger.info(
            f"Safety: cooldown expired. Resetting. "
            f"Was {self._consecutive_losses} consecutive losses."
        )
        self._consecutive_losses = 0
        self._locked_until = 0.0

    def update_config(
        self,
        max_consecutive: int | None = None,
        loss_threshold: float | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        if max_consecutive is not None:
            self.max_consecutive = max_consecutive
        if loss_threshold is not None:
            self.loss_threshold = loss_threshold
        if cooldown_seconds is not None:
            self.cooldown_seconds = cooldown_seconds

    def status(self) -> dict:
        return {
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive": self.max_consecutive,
            "is_locked": self.is_locked,
            "lock_remaining_seconds": round(self.lock_remaining_seconds),
            "loss_threshold": self.loss_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }
