from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from microprofits.api.rest_client import RestClient
from microprofits.api.exceptions import OrderError
from microprofits.data.store import Store
from microprofits.strategy.scalper import EntrySignal

FAST_TP_THRESHOLD = 15.0


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

    @property
    def open_positions(self) -> list[TrackedPosition]:
        return list(self._positions.values())

    def count_for_epic(self, epic: str) -> int:
        return sum(1 for p in self._positions.values() if p.epic == epic)

    def can_open(self, epic: str, max_positions: int, cooldown: float = 30.0) -> bool:
        if self.count_for_epic(epic) >= max_positions:
            return False
        if (time.time() - self._last_entry_time.get(epic, 0)) < cooldown:
            return False
        return True

    def can_open_fast(self, epic: str, max_positions: int) -> bool:
        return self.count_for_epic(epic) < max_positions

    # -- recovery on startup -------------------------------------------------

    async def recover(self) -> None:
        try:
            db_open = await self._store.get_open_trades()
            live_positions = await self._client.get_positions()
        except Exception as e:
            logger.error(f"Recovery failed: {e}")
            return

        live_map = {p.deal_id: p for p in live_positions}

        for trade in db_open:
            try:
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
                    await self._store.save_trade_close(
                        deal_id=deal_id,
                        exit_price=trade.get("profit_level") or trade.get("stop_level") or trade["entry_price"],
                        pnl=pnl,
                        exit_reason="SERVER_CLOSE",
                    )
                    await self._store.log_audit(
                        trade["epic"], "SERVER_CLOSE",
                        {"deal_id": deal_id, "pnl": pnl},
                        pnl=pnl,
                    )
                    await self._store.save_trail_snapshot(
                        epic=trade["epic"], deal_id=deal_id, upl=pnl,
                        peak_upl=pnl if pnl > 0 else 0.0,
                        trail_level=0.0, initial_sl=0.0,
                        trail_pct=0.0, activated=False,
                        event="SERVER_CLOSE",
                    )
                    logger.warning(f"Position {deal_id} closed server-side, pnl={pnl:.2f}")
            except Exception as e:
                logger.error(f"Error recovering trade {trade.get('deal_id', '?')}: {e}")

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
            logger.warning(f"Order rejected for {signal.epic}: {e.reason}")
            try:
                await self._store.log_audit(
                    signal.epic, "ENTRY_REJECTED",
                    {"reason": str(e.reason), "price": signal.entry_price},
                )
            except Exception:
                pass
            return None
        except Exception as e:
            logger.error(f"Unexpected error opening position: {e}")
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

        fill = confirm.level
        sl = signal.stop_level
        tp = signal.profit_level

        try:
            db_id = await self._store.save_trade_open(
                epic=signal.epic,
                deal_id=actual_deal_id,
                direction=signal.direction,
                size=confirm.size,
                entry_price=fill,
                stop_level=sl,
                profit_level=tp,
            )
        except Exception as e:
            logger.error(f"Failed to save trade to DB: {e}")
            db_id = None

        tracked = TrackedPosition(
            deal_id=actual_deal_id,
            epic=signal.epic,
            direction=signal.direction,
            size=confirm.size,
            entry_price=fill,
            stop_level=sl,
            profit_level=tp,
            db_id=db_id,
        )
        self._positions[actual_deal_id] = tracked

        try:
            await self._store.log_audit(
                signal.epic, "ENTRY",
                {"deal_id": actual_deal_id, "price": fill, "size": confirm.size, "sl": sl, "tp": tp},
            )
        except Exception:
            pass

        logger.info(f"Opened {signal.direction} {signal.epic} x{confirm.size} @ {fill} SL={sl} TP={tp}")
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
        try:
            live_positions = await self._client.get_positions()
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return

        live_map = {p.deal_id: p for p in live_positions}
        to_remove: list[str] = []

        for deal_id, tracked in list(self._positions.items()):
            if tracked.epic != epic:
                continue

            try:
                live = live_map.get(deal_id)
                if live is None:
                    # Position closed server-side (TP or SL hit)
                    pnl = tracked.last_upl
                    if pnl == 0:
                        pnl = self._estimate_pnl_tracked(tracked)

                    if pnl < 0:
                        exit_reason = "SL_HIT"
                    elif tracked.profit_level is not None:
                        exit_reason = "TP_HIT"
                    else:
                        # No TP was set — trail or manual close
                        exit_reason = "TRAIL_CLOSE"
                    exit_price = (
                        tracked.profit_level if exit_reason == "TP_HIT" else tracked.stop_level
                    ) or tracked.entry_price
                    seconds_held = time.time() - tracked.opened_ts

                    try:
                        await self._store.save_trade_close(
                            deal_id=deal_id, exit_price=exit_price, pnl=pnl, exit_reason=exit_reason,
                        )
                        await self._store.log_audit(
                            epic, exit_reason, {"deal_id": deal_id, "pnl": pnl}, pnl=pnl,
                        )
                        # Save trail snapshot so the full lifecycle is visible
                        await self._store.save_trail_snapshot(
                            epic=epic, deal_id=deal_id, upl=pnl,
                            peak_upl=pnl if pnl > 0 else 0.0,
                            trail_level=0.0, initial_sl=0.0,
                            trail_pct=0.0, activated=False,
                            event=exit_reason,
                        )
                    except Exception as e:
                        logger.error(f"Failed to log trade close: {e}")

                    to_remove.append(deal_id)
                    logger.info(f"{exit_reason} {epic} deal={deal_id} pnl={pnl:.2f} held {seconds_held:.0f}s")

                    # Fast TP reopen
                    if exit_reason == "TP_HIT" and seconds_held <= FAST_TP_THRESHOLD:
                        logger.info(f"FAST TP in {seconds_held:.1f}s — reopening")
                        if self.can_open_fast(epic, max_positions):
                            try:
                                num_contracts = symbol_cfg.get("num_contracts") or config.get("num_contracts", 1)
                                pt = symbol_cfg.get("profit_target") or config.get("profit_target", 5)
                                sl_cfg = symbol_cfg.get("stop_loss") or config.get("stop_loss", 10)
                                ema_on = config.get("ema_filter_on", False)
                                ema_period = config.get("ema_period", 5)
                                vel_threshold = config.get("velocity_threshold", 0.15)
                                manual = config.get("manual_mode", False)

                                signal = scalper.check_entry(
                                    epic=epic, current_candle=current_candle, history=history,
                                    num_contracts=num_contracts, profit_target=pt, stop_loss=sl_cfg,
                                    min_stop_distance=min_stop_distance, ema_filter_on=ema_on,
                                    ema_period=ema_period, velocity_threshold=vel_threshold,
                                    manual_mode=manual,
                                )
                                if signal:
                                    await self.open_position(signal)
                            except Exception as e:
                                logger.error(f"Fast reopen failed: {e}")
                    continue

                # Update last known UPL
                tracked.last_upl = live.upl

            except Exception as e:
                logger.error(f"Error checking position {deal_id}: {e}")

        for deal_id in to_remove:
            self._positions.pop(deal_id, None)

    def _estimate_pnl_tracked(self, tracked: TrackedPosition) -> float:
        try:
            if tracked.profit_level and tracked.stop_level:
                tp_pnl = (tracked.profit_level - tracked.entry_price) * tracked.size
                sl_pnl = (tracked.stop_level - tracked.entry_price) * tracked.size
                return tp_pnl if tracked.last_upl > 0 else sl_pnl
        except Exception:
            pass
        return 0

    def _estimate_pnl(self, trade: dict) -> float:
        try:
            entry = trade.get("entry_price", 0)
            size = trade.get("size", 1)
            tp = trade.get("profit_level")
            sl = trade.get("stop_level")
            if tp and sl:
                return (tp - entry) * size
        except Exception:
            pass
        return 0
