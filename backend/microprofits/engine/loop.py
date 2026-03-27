from __future__ import annotations

import asyncio

from loguru import logger

from microprofits.api.rest_client import RestClient
from microprofits.api.exceptions import RateLimitError, CapitalAPIError
from microprofits.data.store import Store
from microprofits.engine.position_tracker import PositionTracker
from microprofits.strategy.candle_history import CandleHistory
from microprofits.strategy.scalper import MomentumScalper


class ScalperLoop:
    def __init__(self, client: RestClient, store: Store) -> None:
        self.client = client
        self.store = store
        self.tracker = PositionTracker(client, store)
        self.scalper = MomentumScalper()

        self._histories: dict[str, CandleHistory] = {}
        self._min_stop_distances: dict[str, float] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        await self.tracker.recover()

        symbols = await self.store.list_symbol_configs()
        for sym in symbols:
            await self._init_epic(sym["epic"])

        self._task = asyncio.create_task(self._run())
        logger.info("Scalper loop started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scalper loop stopped")

    async def _init_epic(self, epic: str) -> None:
        if epic not in self._histories:
            self._histories[epic] = CandleHistory(maxlen=30)

        if epic not in self._min_stop_distances:
            try:
                inst = await self.client.get_instrument(epic)
                self._min_stop_distances[epic] = inst.min_stop_distance
                logger.info(f"{epic}: min_stop_distance = {inst.min_stop_distance}")
            except Exception as e:
                logger.warning(f"Failed to get instrument info for {epic}: {e}")
                self._min_stop_distances[epic] = 6.0

        try:
            candles = await self.client.get_prices(epic, "MINUTE", max_bars=30)
            closed = candles[:-1] if candles else []
            self._histories[epic].load(closed)
            logger.info(f"{epic}: loaded {len(closed)} historical candles")
        except Exception as e:
            logger.warning(f"Failed to load candles for {epic}: {e}")

    async def _run(self) -> None:
        backoff = 0
        while self._running:
            try:
                config = await self.store.get_bot_config()
                poll_interval = config.get("poll_interval", 2.0)

                # Sync safety config
                self.tracker.safety.update_config(
                    max_consecutive=config.get("safety_max_consecutive", 10),
                    loss_threshold=config.get("safety_loss_threshold", 1.0),
                    cooldown_seconds=config.get("safety_cooldown", 3600.0),
                )

                if not config.get("enabled", False):
                    await asyncio.sleep(poll_interval)
                    continue

                symbols = await self.store.list_symbol_configs()
                for sym in symbols:
                    if not self._running:
                        break
                    epic = sym["epic"]
                    if not sym.get("enabled", True):
                        continue

                    if epic not in self._histories:
                        await self._init_epic(epic)

                    await self._process_epic(epic, config, sym)

                backoff = 0
                await asyncio.sleep(poll_interval)

            except RateLimitError:
                backoff = min(backoff + 5, 30)
                logger.warning(f"Rate limited — backing off {backoff}s")
                await asyncio.sleep(backoff)
            except CapitalAPIError as e:
                logger.error(f"API error in loop: {e}")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Unexpected error in loop: {e}")
                await asyncio.sleep(5)

    async def _process_epic(self, epic: str, config: dict, symbol_cfg: dict) -> None:
        history = self._histories[epic]
        min_stop = self._min_stop_distances.get(epic, 6.0)

        # Fetch latest candles
        raw = await self.client.get_prices(epic, "MINUTE", max_bars=3)
        if len(raw) < 2:
            return

        closed_candles = raw[:-1]
        current_candle = raw[-1]

        for c in closed_candles:
            history.push(c)

        # Resolve effective config
        num_contracts = symbol_cfg.get("num_contracts") or config.get("num_contracts", 1)
        profit_target = symbol_cfg.get("profit_target") or config.get("profit_target", 5)
        stop_loss = symbol_cfg.get("stop_loss") or config.get("stop_loss", 10)
        max_positions = symbol_cfg.get("max_positions") or config.get("max_positions", 3)
        ema_on = config.get("ema_filter_on", False)
        ema_period = config.get("ema_period", 5)
        cooldown = config.get("entry_cooldown", 30.0)

        # 1. Detect server-side closes (TP/SL hit) + fast reopen
        await self.tracker.check_positions(
            epic=epic,
            profit_target=profit_target,
            max_positions=max_positions,
            scalper=self.scalper,
            current_candle=current_candle,
            history=history,
            config=config,
            symbol_cfg=symbol_cfg,
            min_stop_distance=min_stop,
        )

        # 2. Check for new entry (with cooldown)
        if self.tracker.can_open(epic, max_positions, cooldown):
            signal = self.scalper.check_entry(
                epic=epic,
                current_candle=current_candle,
                history=history,
                num_contracts=num_contracts,
                profit_target=profit_target,
                stop_loss=stop_loss,
                min_stop_distance=min_stop,
                ema_filter_on=ema_on,
                ema_period=ema_period,
            )
            if signal:
                await self.tracker.open_position(signal)
