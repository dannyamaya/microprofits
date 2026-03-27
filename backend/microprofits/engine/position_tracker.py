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

FAST_TP_THRESHOLD = 15.0  # seconds — TP hit within this = fast, reopen instantly


@dataclass
class TrackedPosition:
    deal_id: str
    epic: str
    direction: str
    size: float
    entry_price: float
    stop_level: float | None
    profit_level: float | None
    db_id: int | None = None
    opened_at: datetime = field(default_factory=datetime.utcnow)
    opened_ts: float = field(default_factory=time.time)
    last_upl: float = 0.0


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

    def can_open_fast(self, epic: str, max_positions: int) -> bool:
        """Fast reopen after quick TP — no cooldown, only check count + safety."""
        if self.safety.is_locked:
            return False
        return self.count_for_epic(epic) < max_positions

    # -- recovery on startup -------------------------------------------------

    async def recover(self) -> None:
        db_open = await self._store.get_open_trades()
        live_positions = await self._client.get_positions()
        live_map = {p.deal_id: p for p in live_positions}

        for trade in db_open:
            deal_id = trade["deal_id"]
            live = live_map.get(deal_id)
            if live is not None:
                self._positions[deal_id] = TrackedPosition(
                    deal_id=deal_id,
                    epic=trade["epic"],
                    direction=trade["direction"],
                    size=trade["size"],
                    entry_price=trade["entry_price"],
                    stop_level=trade.get("stop_level"),
                    profit_level=trade.get("profit_level"),
                    db_id=trade["id"],
                    opened_at=trade["opened_at"],
                    last_upl=live.upl,
                )
                logger.info(f"Recovered position {deal_id} for {trade['epic']}")
            else:
                pnl = self._estimate_pnl(trade)
                exit_reason = "TP_HIT" if pnl >= 0 else "SL_HIT"
                await self._store.save_trade_close(
                    deal_id=deal_id,
                    exit_price=trade.get("profit_level") or trade.get("stop_level") or trade["entry_price"],
                    pnl=pnl,
                    exit_reason="SERVER_CLOSE",
                )
                self.safety.record_trade(pnl)
                await self._store.log_audit(
                    trade["epic"], "SERVER_CLOSE",
                    {"deal_id": deal_id, "pnl": pnl},
                    pnl=pnl,
                )
                logger.warning(f"Position {deal_id} closed server-side, pnl={pnl:.2f}")

    # -- open position -------------------------------------------------------

    async def open_position(self, signal: EntrySignal) -> TrackedPosition | None:
        try:
            confirm = await self._client.open_position(
                epic=signal.epic,
                direction=signal.direction,
                size=signal.size,
                stop_level=signal.stop_level,
                profit_level=signal.profit_level,
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

        # Use preliminary SL/TP as-is — no correction.
        # Correcting from fill price can tighten SL into current price
        # and cause instant stop-outs. The 1-3 point variance from
        # candle vs fill is acceptable.
        fill = confirm.level

        db_id = await self._store.save_trade_open(
            epic=signal.epic,
            deal_id=actual_deal_id,
            direction=signal.direction,
            size=confirm.size,
            entry_price=fill,
            stop_level=signal.stop_level,
            profit_level=signal.profit_level,
        )

        tracked = TrackedPosition(
            deal_id=actual_deal_id,
            epic=signal.epic,
            direction=signal.direction,
            size=confirm.size,
            entry_price=fill,
            stop_level=signal.stop_level,
            profit_level=signal.profit_level,
            db_id=db_id,
        )
        self._positions[actual_deal_id] = tracked

        await self._store.log_audit(
            signal.epic, "ENTRY",
            {
                "deal_id": actual_deal_id,
                "price": fill,
                "size": confirm.size,
                "sl": correct_sl,
                "tp": correct_tp,
            },
        )
        logger.info(
            f"Opened {signal.direction} {signal.epic} x{confirm.size} @ {fill} "
            f"SL={correct_sl} TP={correct_tp}"
        )
        return tracked

    # -- check positions: detect server-side closes (TP/SL hit) --------------

    async def check_positions(
        self,
        epic: str,
        profit_target: float,
        max_positions: int,
        scalper,
        current_candle,
        history,
        config: dict,
        symbol_cfg: dict,
        min_stop_distance: float,
    ) -> None:
        live_positions = await self._client.get_positions()
        live_map = {p.deal_id: p for p in live_positions}

        to_remove: list[str] = []

        for deal_id, tracked in list(self._positions.items()):
            if tracked.epic != epic:
                continue

            live = live_map.get(deal_id)
            if live is None:
                # Position closed server-side (TP or SL hit by Capital.com)
                pnl = tracked.last_upl
                if pnl == 0:
                    pnl = self._estimate_pnl_tracked(tracked)

                exit_reason = "TP_HIT" if pnl >= 0 else "SL_HIT"
                exit_price = (
                    tracked.profit_level if pnl >= 0 else tracked.stop_level
                ) or tracked.entry_price

                await self._store.save_trade_close(
                    deal_id=deal_id,
                    exit_price=exit_price,
                    pnl=pnl,
                    exit_reason=exit_reason,
                )
                self.safety.record_trade(pnl)
                await self._store.log_audit(
                    epic, exit_reason,
                    {"deal_id": deal_id, "pnl": pnl},
                    pnl=pnl,
                )
                to_remove.append(deal_id)

                # Check if this was a fast TP — reopen instantly
                seconds_held = time.time() - tracked.opened_ts
                if exit_reason == "TP_HIT" and seconds_held <= FAST_TP_THRESHOLD:
                    logger.info(
                        f"FAST TP {epic} deal={deal_id} pnl={pnl:.2f} "
                        f"in {seconds_held:.1f}s — reopening instantly"
                    )
                    if self.can_open_fast(epic, max_positions):
                        num_contracts = symbol_cfg.get("num_contracts") or config.get("num_contracts", 1)
                        pt = symbol_cfg.get("profit_target") or config.get("profit_target", 5)
                        sl = symbol_cfg.get("stop_loss") or config.get("stop_loss", 10)
                        ema_on = config.get("ema_filter_on", False)
                        ema_period = config.get("ema_period", 5)

                        signal = scalper.check_entry(
                            epic=epic,
                            current_candle=current_candle,
                            history=history,
                            num_contracts=num_contracts,
                            profit_target=pt,
                            stop_loss=sl,
                            min_stop_distance=min_stop_distance,
                            ema_filter_on=ema_on,
                            ema_period=ema_period,
                        )
                        if signal:
                            await self.open_position(signal)
                            await self._store.log_audit(
                                epic, "FAST_REOPEN",
                                {"seconds_held": round(seconds_held, 1), "pnl": pnl},
                            )
                else:
                    logger.info(
                        f"{exit_reason} {epic} deal={deal_id} pnl={pnl:.2f} "
                        f"held {seconds_held:.0f}s"
                    )
                continue

            # Update last known UPL
            tracked.last_upl = live.upl

        for deal_id in to_remove:
            self._positions.pop(deal_id, None)

    def _estimate_pnl_tracked(self, tracked: TrackedPosition) -> float:
        if tracked.profit_level and tracked.stop_level:
            tp_pnl = (tracked.profit_level - tracked.entry_price) * tracked.size
            sl_pnl = (tracked.stop_level - tracked.entry_price) * tracked.size
            if tracked.last_upl > 0:
                return tp_pnl
            return sl_pnl
        return 0

    def _estimate_pnl(self, trade: dict) -> float:
        entry = trade.get("entry_price", 0)
        size = trade.get("size", 1)
        tp = trade.get("profit_level")
        sl = trade.get("stop_level")
        if tp and sl:
            return (tp - entry) * size
        return 0
