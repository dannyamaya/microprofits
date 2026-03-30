"""Percentage-based trailing stop — independent 2-second loop.

Managed 100% by the backend. Does NOT modify SL on Capital.com.
Capital.com SL stays as the original safety net (worst case).

For each open position whose symbol has trail_pct > 0:
  - Track the peak UPL (high water mark)
  - When UPL sets a new peak, raise the internal trail level:
      trail_level += delta * (trail_pct / 100)
  - Trail level only goes up, never down
  - When UPL drops below trail_level → close the position via API
  - No TP on Capital — the bot decides when to exit

Formula per tick:
    if upl > peak:
        delta = upl - peak
        trail_level = trail_level + delta * (trail_pct / 100)
        peak = upl
    elif upl <= trail_level and trail_level > initial_sl:
        CLOSE position
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

from microprofits.api.rest_client import RestClient
from microprofits.data.store import Store


@dataclass
class TrailState:
    """Per-position trailing state."""
    peak_upl: float = 0.0
    trail_level: float = 0.0    # internal SL in dollar P&L terms
    initial_sl: float = 0.0     # original SL from Capital (safety net)
    activated: bool = False      # trail only activates once UPL > 0


class PctTrailer:
    """Monitors open positions every 2s, closes when trail is hit."""

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
        logger.info("PctTrailer started (2s interval, backend-managed)")

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
        # Skip trail entirely in manual mode
        config = await self._store.get_bot_config()
        if config.get("manual_mode", False):
            return

        symbols = await self._store.list_symbol_configs()
        trail_map: dict[str, tuple[float, str]] = {}  # epic -> (trail_pct, account_id)
        for sym in symbols:
            pct = sym.get("trail_pct") or 0
            if pct > 0 and sym.get("enabled", True):
                trail_map[sym["epic"]] = (pct, sym.get("account_id", ""))

        if not trail_map:
            return

        account_epics: dict[str, list[str]] = {}
        for epic, (_, account_id) in trail_map.items():
            account_epics.setdefault(account_id, []).append(epic)

        all_live_deal_ids: set[str] = set()

        for account_id, epics in account_epics.items():
            client = self._clients.get(account_id)
            if client is None:
                continue

            try:
                live_positions = await client.get_positions()
            except Exception as e:
                logger.error(f"PctTrailer: failed to get positions for account {account_id}: {e}")
                continue

            for pos in live_positions:
                all_live_deal_ids.add(pos.deal_id)
                if pos.epic not in trail_map:
                    continue

                trail_pct, _ = trail_map[pos.epic]
                await self._process_position(client, pos, trail_pct)

        # Clean up trail state for positions that no longer exist
        gone = [did for did in self._trail if did not in all_live_deal_ids]
        for did in gone:
            self._trail.pop(did, None)

    async def _process_position(
        self, client: RestClient, pos, trail_pct: float
    ) -> None:
        deal_id = pos.deal_id
        upl = pos.upl

        # Initialize trail state if new position
        if deal_id not in self._trail:
            initial_sl = self._calc_sl_dollars(pos)
            self._trail[deal_id] = TrailState(
                peak_upl=0.0,
                trail_level=initial_sl,  # starts at original SL level
                initial_sl=initial_sl,
                activated=False,
            )
            logger.info(
                f"PctTrailer: tracking {pos.epic} deal={deal_id} "
                f"initial_sl=${initial_sl:.2f} trail_pct={trail_pct}%"
            )
            await self._store.save_trail_snapshot(
                pos.epic, deal_id, upl, 0.0, initial_sl, initial_sl,
                trail_pct, False, "INIT",
            )

        state = self._trail[deal_id]

        # New peak → raise trail level
        if upl > state.peak_upl:
            delta = upl - state.peak_upl
            sl_bump = delta * (trail_pct / 100.0)
            old_trail = state.trail_level
            state.trail_level += sl_bump
            state.peak_upl = upl

            if not state.activated and upl > 0:
                state.activated = True

            logger.info(
                f"PctTrailer: {pos.epic} deal={deal_id} "
                f"new peak=${upl:.2f} trail ${old_trail:.2f} -> ${state.trail_level:.2f}"
            )

            await self._store.log_audit(
                pos.epic, "PCT_TRAIL_MOVE",
                {
                    "deal_id": deal_id,
                    "peak_upl": round(upl, 2),
                    "delta": round(delta, 2),
                    "trail_old": round(old_trail, 2),
                    "trail_new": round(state.trail_level, 2),
                    "trail_pct": trail_pct,
                },
            )
            await self._store.save_trail_snapshot(
                pos.epic, deal_id, upl, state.peak_upl, state.trail_level,
                state.initial_sl, trail_pct, state.activated, "TRAIL_MOVE",
            )
            return

        # Check if trail level is hit — only close if trail has been activated
        # and trail_level has moved above the initial SL (otherwise let Capital handle it)
        if (
            state.activated
            and state.trail_level > state.initial_sl
            and upl <= state.trail_level
        ):
            logger.info(
                f"PctTrailer: CLOSING {pos.epic} deal={deal_id} "
                f"UPL=${upl:.2f} hit trail_level=${state.trail_level:.2f} "
                f"(peak was ${state.peak_upl:.2f})"
            )

            try:
                await client.close_position(deal_id)

                pnl = upl  # UPL at time of close is approximate P&L
                await self._store.log_audit(
                    pos.epic, "PCT_TRAIL_EXIT",
                    {
                        "deal_id": deal_id,
                        "upl_at_close": round(upl, 2),
                        "trail_level": round(state.trail_level, 2),
                        "peak_upl": round(state.peak_upl, 2),
                        "trail_pct": trail_pct,
                    },
                    pnl=pnl,
                )
                await self._store.save_trail_snapshot(
                    pos.epic, deal_id, upl, state.peak_upl, state.trail_level,
                    state.initial_sl, trail_pct, state.activated, "EXIT",
                )

                # Remove from tracking (will also be cleaned up next tick)
                self._trail.pop(deal_id, None)

            except Exception as e:
                logger.error(f"PctTrailer: failed to close {deal_id}: {e}")

    @staticmethod
    def _calc_sl_dollars(pos) -> float:
        """Calculate current SL in dollar P&L terms."""
        if pos.stop_level is None:
            return 0.0
        if pos.direction == "BUY":
            return (pos.stop_level - pos.open_level) * pos.size
        else:  # SELL
            return (pos.open_level - pos.stop_level) * pos.size
