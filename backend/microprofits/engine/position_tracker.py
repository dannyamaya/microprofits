from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from microprofits.api.models import Position
from microprofits.api.rest_client import RestClient
from microprofits.api.exceptions import OrderError
from microprofits.data.store import Store
from microprofits.strategy.scalper import EntrySignal


@dataclass
class TrackedPosition:
    deal_id: str
    epic: str
    direction: str
    size: float
    entry_price: float
    stop_level: float | None
    db_id: int | None = None
    opened_at: datetime = field(default_factory=datetime.utcnow)
    last_upl: float = 0.0
    trail_locks: int = 0  # how many profit_target increments we've locked
    breakeven_hit: bool = False  # SL moved to entry price at half profit_target


class PositionTracker:
    def __init__(self, client: RestClient, store: Store) -> None:
        self._client = client
        self._store = store
        self._positions: dict[str, TrackedPosition] = {}
        self._last_entry_time: dict[str, float] = {}
        self._last_rejection_time: dict[str, float] = {}  # backoff after rejections

    @property
    def open_positions(self) -> list[TrackedPosition]:
        return list(self._positions.values())

    def count_for_epic(self, epic: str) -> int:
        return sum(1 for p in self._positions.values() if p.epic == epic)

    def can_open(self, epic: str, max_positions: int, cooldown: float = 30.0) -> bool:
        if self.count_for_epic(epic) >= max_positions:
            return False
        now = time.time()
        # Cooldown after last entry
        if (now - self._last_entry_time.get(epic, 0)) < cooldown:
            return False
        # Backoff after rejection (60s)
        if (now - self._last_rejection_time.get(epic, 0)) < 60:
            return False
        return True

    # -- recovery on startup -------------------------------------------------

    async def recover(self) -> None:
        db_open = await self._store.get_open_trades()
        live_positions = await self._client.get_positions()
        live_map = {p.deal_id: p for p in live_positions}

        for trade in db_open:
            deal_id = trade["deal_id"]
            live = live_map.get(deal_id)
            if live is not None:
                # Estimate trail_locks from current SL vs entry
                entry = trade["entry_price"]
                current_sl = live.stop_level or trade.get("stop_level") or entry
                profit_target = 5.0  # will be overridden by config each poll
                distance = current_sl - entry
                trail_locks = max(0, int(distance / profit_target)) if distance > 0 else 0

                self._positions[deal_id] = TrackedPosition(
                    deal_id=deal_id,
                    epic=trade["epic"],
                    direction=trade["direction"],
                    size=trade["size"],
                    entry_price=entry,
                    stop_level=current_sl,
                    db_id=trade["id"],
                    opened_at=trade["opened_at"],
                    last_upl=live.upl,
                    trail_locks=trail_locks,
                )
                logger.info(f"Recovered position {deal_id} for {trade['epic']} (trail_locks={trail_locks})")
            else:
                pnl = self._calculate_pnl_from_levels(trade)
                exit_reason = "TP_HIT" if pnl >= 0 else "SL_HIT"
                exit_price = trade.get("stop_level") or trade["entry_price"]
                await self._store.save_trade_close(
                    deal_id=deal_id,
                    exit_price=exit_price,
                    pnl=pnl,
                    exit_reason="SERVER_CLOSE",
                )
                await self._store.log_audit(
                    trade["epic"], "SERVER_CLOSE",
                    {"deal_id": deal_id, "pnl": pnl, "note": "closed while bot was down"},
                    pnl=pnl,
                )
                logger.warning(f"Position {deal_id} closed server-side, estimated pnl={pnl:.2f}")

    # -- open position (NO profitLevel) --------------------------------------

    async def open_position(self, signal: EntrySignal) -> TrackedPosition | None:
        try:
            confirm = await self._client.open_position(
                epic=signal.epic,
                direction=signal.direction,
                size=signal.size,
                stop_level=signal.stop_level,
                profit_level=None,  # NO TP — we trail instead
            )
        except OrderError as e:
            logger.error(f"Order rejected for {signal.epic}: {e.reason}")
            self._last_rejection_time[signal.epic] = time.time()
            await self._store.log_audit(
                signal.epic, "ENTRY_REJECTED",
                {"reason": e.reason, "price": signal.entry_price},
            )
            return None

        self._last_entry_time[signal.epic] = time.time()

        # Reconcile deal_id
        actual_deal_id = confirm.deal_id
        try:
            await asyncio.sleep(1)
            live = await self._client.get_positions()
            for p in live:
                if (
                    p.epic == signal.epic
                    and p.direction == signal.direction
                    and p.deal_id not in self._positions
                ):
                    actual_deal_id = p.deal_id
                    break
        except Exception:
            pass

        db_id = await self._store.save_trade_open(
            epic=signal.epic,
            deal_id=actual_deal_id,
            direction=signal.direction,
            size=confirm.size,
            entry_price=confirm.level,
            stop_level=signal.stop_level,
            profit_level=None,
        )

        tracked = TrackedPosition(
            deal_id=actual_deal_id,
            epic=signal.epic,
            direction=signal.direction,
            size=confirm.size,
            entry_price=confirm.level,
            stop_level=signal.stop_level,
            db_id=db_id,
        )
        self._positions[actual_deal_id] = tracked

        await self._store.log_audit(
            signal.epic, "ENTRY",
            {
                "deal_id": actual_deal_id,
                "price": confirm.level,
                "size": confirm.size,
                "sl": signal.stop_level,
            },
        )
        logger.info(
            f"Opened {signal.direction} {signal.epic} x{confirm.size} @ {confirm.level} "
            f"SL={signal.stop_level} (trailing mode, no TP)"
        )
        return tracked

    # -- check positions: trail SL + detect server-side closes ---------------

    async def check_positions(
        self,
        epic: str,
        profit_target: float,
        num_contracts: float,
        config: dict,
    ) -> None:
        live_positions = await self._client.get_positions()
        live_map = {p.deal_id: p for p in live_positions}

        to_remove: list[str] = []

        for deal_id, tracked in list(self._positions.items()):
            if tracked.epic != epic:
                continue

            live = live_map.get(deal_id)
            if live is None:
                # Position closed server-side (SL hit after trailing)
                pnl = tracked.last_upl
                if pnl == 0 and tracked.trail_locks > 0:
                    # We had locked profit — SL was at entry + (trail_locks * tp_distance)
                    tp_distance = profit_target / num_contracts
                    pnl = tracked.trail_locks * tp_distance * tracked.size
                elif pnl == 0:
                    pnl = self._calculate_pnl_from_sl(tracked)

                exit_reason = "TRAIL_SL" if tracked.trail_locks > 0 else "SL_HIT"
                exit_price = tracked.stop_level or tracked.entry_price
                await self._store.save_trade_close(
                    deal_id=deal_id,
                    exit_price=exit_price,
                    pnl=pnl,
                    exit_reason=exit_reason,
                )
                await self._store.log_audit(
                    epic, exit_reason,
                    {"deal_id": deal_id, "pnl": pnl, "trail_locks": tracked.trail_locks},
                    pnl=pnl,
                )
                to_remove.append(deal_id)
                logger.info(
                    f"{exit_reason} {epic} deal={deal_id} pnl={pnl:.2f} "
                    f"(trailed {tracked.trail_locks} times)"
                )
                continue

            # Update last known UPL
            tracked.last_upl = live.upl

            # --- Breakeven at half profit_target ---
            half_target = profit_target / 2.0
            if not tracked.breakeven_hit and live.upl >= half_target:
                be_sl = round(tracked.entry_price, 2)
                try:
                    await self._client.update_position_fast(
                        deal_id=deal_id,
                        stop_level=be_sl,
                    )
                    tracked.breakeven_hit = True
                    tracked.stop_level = be_sl
                    await self._store.log_audit(
                        epic, "BREAKEVEN",
                        {"deal_id": deal_id, "upl": live.upl, "new_sl": be_sl},
                    )
                    logger.info(
                        f"BREAKEVEN {epic} deal={deal_id}: "
                        f"UPL=${live.upl:.2f} >= ${half_target:.2f} → SL moved to entry {be_sl}"
                    )
                except OrderError as e:
                    logger.error(f"Failed to set breakeven for {deal_id}: {e}")

            # --- Trailing SL logic ---
            # tp_distance = points per profit_target dollar
            tp_distance = profit_target / num_contracts
            # How many increments has price moved above entry?
            price_above_entry = live.open_level + (live.upl / live.size) - tracked.entry_price if live.size else 0
            expected_locks = int(price_above_entry / tp_distance)

            if expected_locks > tracked.trail_locks:
                # Price crossed a new profit level — move SL up
                new_lock = expected_locks
                new_sl = tracked.entry_price + (new_lock * tp_distance)
                new_sl = round(new_sl, 2)

                try:
                    await self._client.update_position_fast(
                        deal_id=deal_id,
                        stop_level=new_sl,
                    )
                    old_locks = tracked.trail_locks
                    tracked.trail_locks = new_lock
                    tracked.stop_level = new_sl
                    locked_profit = new_lock * profit_target

                    await self._store.log_audit(
                        epic, "TRAIL_MOVE",
                        {
                            "deal_id": deal_id,
                            "old_locks": old_locks,
                            "new_locks": new_lock,
                            "new_sl": new_sl,
                            "locked_profit": locked_profit,
                        },
                    )
                    logger.info(
                        f"TRAIL {epic} deal={deal_id}: "
                        f"SL moved to {new_sl} (locked ${locked_profit:.2f}, "
                        f"{new_lock} jumps)"
                    )
                except OrderError as e:
                    logger.error(f"Failed to trail SL for {deal_id}: {e}")

        for deal_id in to_remove:
            self._positions.pop(deal_id, None)

    def _calculate_pnl_from_sl(self, tracked: TrackedPosition) -> float:
        """Estimate P&L from SL level (position was stopped out)."""
        if tracked.stop_level:
            return (tracked.stop_level - tracked.entry_price) * tracked.size
        return 0

    def _calculate_pnl_from_levels(self, trade: dict) -> float:
        entry = trade.get("entry_price", 0)
        size = trade.get("size", 1)
        sl = trade.get("stop_level")
        if sl:
            return (sl - entry) * size
        return 0
