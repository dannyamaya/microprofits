"""Percentage-based trailing stop — independent 2-second loop.

For each open position whose symbol has trail_pct > 0:
  - Track the peak UPL (high water mark)
  - When UPL sets a new peak, move SL up by: delta * trail_pct
  - SL only moves up, never down
  - No TP — let the trail exit the position

Formula per tick:
    if upl > peak:
        delta = upl - peak
        new_sl = current_sl + delta * (trail_pct / 100)
        peak = upl
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger

from microprofits.api.rest_client import RestClient
from microprofits.data.store import Store


@dataclass
class TrailState:
    """Per-position trailing state."""
    peak_upl: float = 0.0
    current_sl: float = 0.0  # in dollar distance from entry (negative = loss)


class PctTrailer:
    """Monitors open positions every 2s and adjusts SL by trail_pct."""

    INTERVAL = 2.0  # seconds

    def __init__(self, store: Store) -> None:
        self._store = store
        self._clients: dict[str, RestClient] = {}   # account_id -> client
        self._trail: dict[str, TrailState] = {}      # deal_id -> trail state
        self._running = False
        self._task: asyncio.Task | None = None

    def register_client(self, account_id: str, client: RestClient) -> None:
        self._clients[account_id] = client

    @property
    def trail_states(self) -> dict[str, TrailState]:
        return dict(self._trail)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("PctTrailer started (2s interval)")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._trail.clear()
        logger.info("PctTrailer stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"PctTrailer error: {e}")
            await asyncio.sleep(self.INTERVAL)

    async def _tick(self) -> None:
        # Load symbol configs to know which epics have trail_pct > 0
        symbols = await self._store.list_symbol_configs()
        trail_map: dict[str, tuple[float, str]] = {}  # epic -> (trail_pct, account_id)
        for sym in symbols:
            pct = sym.get("trail_pct") or 0
            if pct > 0 and sym.get("enabled", True):
                trail_map[sym["epic"]] = (pct, sym.get("account_id", ""))

        if not trail_map:
            return

        # Group epics by account_id
        account_epics: dict[str, list[str]] = {}
        for epic, (_, account_id) in trail_map.items():
            account_epics.setdefault(account_id, []).append(epic)

        # For each account, fetch positions and process
        for account_id, epics in account_epics.items():
            client = self._clients.get(account_id)
            if client is None:
                continue

            try:
                live_positions = await client.get_positions()
            except Exception as e:
                logger.error(f"PctTrailer: failed to get positions for account {account_id}: {e}")
                continue

            # Build set of live deal_ids for cleanup
            live_deal_ids = {p.deal_id for p in live_positions}

            for pos in live_positions:
                if pos.epic not in trail_map:
                    continue

                trail_pct, _ = trail_map[pos.epic]
                await self._process_position(client, pos, trail_pct)

            # Clean up trail state for positions that are gone
            gone = [did for did in list(self._trail.keys())
                    if did not in live_deal_ids]
            for did in gone:
                self._trail.pop(did, None)

    async def _process_position(
        self, client: RestClient, pos, trail_pct: float
    ) -> None:
        deal_id = pos.deal_id
        upl = pos.upl

        # Initialize trail state if new position
        if deal_id not in self._trail:
            # Calculate current SL as dollar distance from entry
            # For BUY: sl_dollars = (stop_level - entry) * size (negative if below entry)
            # We track SL in dollar P&L terms for simplicity
            initial_sl = self._calc_sl_dollars(pos)
            self._trail[deal_id] = TrailState(peak_upl=0.0, current_sl=initial_sl)
            logger.info(
                f"PctTrailer: tracking {pos.epic} deal={deal_id} "
                f"initial_sl=${initial_sl:.2f} trail_pct={trail_pct}%"
            )

        state = self._trail[deal_id]

        # Only act when UPL sets a new high
        if upl <= state.peak_upl:
            return

        # New peak! Calculate delta and move SL
        delta = upl - state.peak_upl
        sl_bump = delta * (trail_pct / 100.0)
        new_sl_dollars = state.current_sl + sl_bump

        # Calculate the new stop_level price from the dollar SL
        new_stop_price = self._dollars_to_stop_price(pos, new_sl_dollars)

        # Only move SL up (higher price for BUY, lower price for SELL)
        current_stop = pos.stop_level
        if current_stop is not None:
            if pos.direction == "BUY" and new_stop_price <= current_stop:
                # Update peak but don't move SL down
                state.peak_upl = upl
                return
            if pos.direction == "SELL" and new_stop_price >= current_stop:
                state.peak_upl = upl
                return

        # Fire-and-forget SL update
        try:
            actual_sl = await client.update_position_fast(deal_id, new_stop_price)
            old_sl = state.current_sl
            state.current_sl = new_sl_dollars
            state.peak_upl = upl

            logger.info(
                f"PctTrailer: {pos.epic} deal={deal_id} "
                f"peak=${upl:.2f} delta=${delta:.2f} "
                f"SL ${old_sl:.2f} -> ${new_sl_dollars:.2f} "
                f"(price={actual_sl})"
            )

            await self._store.log_audit(
                pos.epic, "PCT_TRAIL_MOVE",
                {
                    "deal_id": deal_id,
                    "peak_upl": round(upl, 2),
                    "delta": round(delta, 2),
                    "sl_old": round(old_sl, 2),
                    "sl_new": round(new_sl_dollars, 2),
                    "stop_price": actual_sl,
                    "trail_pct": trail_pct,
                },
            )
        except Exception as e:
            # Still update peak so we don't retry the same delta
            state.peak_upl = upl
            logger.error(f"PctTrailer: failed to update SL for {deal_id}: {e}")

    @staticmethod
    def _calc_sl_dollars(pos) -> float:
        """Calculate current SL in dollar P&L terms."""
        if pos.stop_level is None:
            return 0.0
        if pos.direction == "BUY":
            return (pos.stop_level - pos.open_level) * pos.size
        else:  # SELL
            return (pos.open_level - pos.stop_level) * pos.size

    @staticmethod
    def _dollars_to_stop_price(pos, sl_dollars: float) -> float:
        """Convert dollar SL back to a price level."""
        if pos.size == 0:
            return pos.open_level
        if pos.direction == "BUY":
            return pos.open_level + (sl_dollars / pos.size)
        else:  # SELL
            return pos.open_level - (sl_dollars / pos.size)
