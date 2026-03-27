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
from microprofits.engine.safety import SafetyLock
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
        self._last_rejection_time: dict[str, float] = {}
        self.safety = SafetyLock()

    @property
    def open_positions(self) -> list[TrackedPosition]:
        return list(self._positions.values())

    def count_for_epic(self, epic: str) -> int:
        return sum(1 for p in self._positions.values() if p.epic == epic)

    def can_open(self, epic: str, max_positions: int, cooldown: float = 30.0) -> bool:
        if self.safety.is_locked:
            return False
        if self.count_for_epic(epic) >= max_positions:
            return False
        now = time.time()
        if (now - self._last_entry_time.get(epic, 0)) < cooldown:
            return False
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
                self.safety.record_trade(pnl)
                logger.warning(f"Position {deal_id} closed server-side, estimated pnl={pnl:.2f}")

    # -- open position (NO profitLevel) --------------------------------------

    async def open_position(self, signal: EntrySignal) -> TrackedPosition | None:
        # Calculate SL from a preliminary price for the order
        # (will be corrected after fill)
        preliminary_sl = round(signal.entry_price - signal.sl_distance, 2)

        try:
            confirm = await self._client.open_position(
                epic=signal.epic,
                direction=signal.direction,
                size=signal.size,
                stop_level=preliminary_sl,
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

        # Calculate correct SL from ACTUAL fill price
        actual_sl = round(confirm.level - signal.sl_distance, 2)

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

        # Only correct SL if it gives MORE room (moves SL further from entry)
        # Never tighten — if fill was above candle price, preliminary SL is safer
        if actual_sl < preliminary_sl:
            try:
                await self._client.update_position_fast(actual_deal_id, actual_sl)
            except Exception:
                actual_sl = preliminary_sl  # keep preliminary if update fails
        else:
            actual_sl = preliminary_sl  # keep the lower (safer) SL

        db_id = await self._store.save_trade_open(
            epic=signal.epic,
            deal_id=actual_deal_id,
            direction=signal.direction,
            size=confirm.size,
            entry_price=confirm.level,
            stop_level=actual_sl,
            profit_level=None,
        )

        tracked = TrackedPosition(
            deal_id=actual_deal_id,
            epic=signal.epic,
            direction=signal.direction,
            size=confirm.size,
            entry_price=confirm.level,
            stop_level=actual_sl,
            db_id=db_id,
        )
        self._positions[actual_deal_id] = tracked

        await self._store.log_audit(
            signal.epic, "ENTRY",
            {
                "deal_id": actual_deal_id,
                "price": confirm.level,
                "size": confirm.size,
                "sl": actual_sl,
            },
        )
        logger.info(
            f"Opened {signal.direction} {signal.epic} x{confirm.size} @ {confirm.level} "
            f"SL={actual_sl} (trailing mode, no TP)"
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
                self.safety.record_trade(pnl)
                to_remove.append(deal_id)
                logger.info(
                    f"{exit_reason} {epic} deal={deal_id} pnl={pnl:.2f} "
                    f"(trailed {tracked.trail_locks} times)"
                )
                continue

            # Update last known UPL
            tracked.last_upl = live.upl

            # --- Single unified SL logic: breakeven + trail in one decision ---
            tp_distance = profit_target / num_contracts
            price_above_entry = live.open_level + (live.upl / live.size) - tracked.entry_price if live.size else 0
            expected_locks = int(price_above_entry / tp_distance)

            # Determine best SL level right now
            new_sl: float | None = None
            event: str = ""

            if expected_locks > tracked.trail_locks:
                # Full trail jump(s) — go straight to the highest lock
                new_sl = round(tracked.entry_price + (expected_locks * tp_distance), 2)
                event = "TRAIL_MOVE"
            elif not tracked.breakeven_hit and live.upl >= (profit_target / 2.0):
                # Between half-target and first full lock — breakeven only
                new_sl = round(tracked.entry_price, 2)
                event = "BREAKEVEN"

            if new_sl is not None and new_sl > (tracked.stop_level or 0):
                try:
                    actual_sl = await self._client.update_position_fast(
                        deal_id=deal_id,
                        stop_level=new_sl,
                    )
                    old_locks = tracked.trail_locks
                    if event == "TRAIL_MOVE":
                        # Calculate actual locks based on what Capital.com accepted
                        actual_distance = actual_sl - tracked.entry_price
                        actual_locks = max(0, int(actual_distance / tp_distance))
                        tracked.trail_locks = actual_locks if actual_locks > 0 else expected_locks
                    tracked.breakeven_hit = True
                    tracked.stop_level = actual_sl
                    locked_profit = tracked.trail_locks * profit_target

                    await self._store.log_audit(
                        epic, event,
                        {
                            "deal_id": deal_id,
                            "old_locks": old_locks,
                            "new_locks": tracked.trail_locks,
                            "new_sl": new_sl,
                            "locked_profit": locked_profit,
                            "upl": live.upl,
                        },
                    )
                    if event == "TRAIL_MOVE":
                        logger.info(
                            f"TRAIL {epic} deal={deal_id}: "
                            f"SL → {new_sl} (locked ${locked_profit:.2f}, "
                            f"{old_locks} → {tracked.trail_locks} jumps, UPL=${live.upl:.2f})"
                        )
                    else:
                        logger.info(
                            f"BREAKEVEN {epic} deal={deal_id}: "
                            f"SL → {new_sl} (UPL=${live.upl:.2f})"
                        )
                except OrderError as e:
                    logger.error(f"Failed to update SL for {deal_id}: {e}")

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
