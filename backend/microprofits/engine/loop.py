from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger

# Scalper only trades in hours with proven positive expectancy (UTC).
# Based on historical analysis of ~3000 trades (Mar-Apr 2026).
# Asian Range has its own schedule and is NOT affected by this.
SCALPER_SAFE_HOURS: frozenset[int] = frozenset({0, 1, 6, 13, 14, 15, 16, 19})

from microprofits.api.rest_client import RestClient
from microprofits.config.settings import settings
from microprofits.api.exceptions import RateLimitError, CapitalAPIError
from microprofits.data.store import Store
from microprofits.engine.position_tracker import PositionTracker
from microprofits.strategy.candle_history import CandleHistory
from microprofits.strategy.scalper import MomentumScalper
from microprofits.strategy.asian_range import AsianRangeBreakout
from microprofits.strategy.bias_provider import BiasProvider
from microprofits.strategy.bias_strategy import BiasStrategy
from microprofits.strategy.discord_notifier import DiscordNotifier
from microprofits.strategy.x_headlines import XHeadlinesScraper
from microprofits.engine.pct_trailer import PctTrailer


class ScalperLoop:
    def __init__(self, client: RestClient, store: Store) -> None:
        self.client = client  # default client (uses CAPITAL_ACCOUNT_ID from .env)
        self.store = store
        self.tracker = PositionTracker(client, store)
        self.scalper = MomentumScalper()
        self._asian_range: dict[str, AsianRangeBreakout] = {}

        # Per-account clients and trackers (keyed by account_id)
        self._clients: dict[str, RestClient] = {}
        self._trackers: dict[str, PositionTracker] = {}

        self._histories: dict[str, CandleHistory] = {}
        self._min_stop_distances: dict[str, float] = {}
        self._pct_trailer = PctTrailer(store)
        self.bias_provider = BiasProvider(store)
        self.bias_strategy: BiasStrategy | None = None
        self.discord = DiscordNotifier()
        self._bias_client: RestClient | None = None
        self._bias_tracker: PositionTracker | None = None
        self._bias_monitor_task: asyncio.Task | None = None
        self._bias_expired_logged: set[str] = set()  # deal_ids already logged as expired
        self.x_scraper = XHeadlinesScraper(store)
        self._headline_task: asyncio.Task | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    def _get_client(self, account_id: str = "") -> RestClient:
        """Get RestClient for a specific account (or default)."""
        if not account_id:
            return self.client
        return self._clients.get(account_id, self.client)

    def _get_tracker(self, account_id: str = "") -> PositionTracker:
        """Get PositionTracker for a specific account (or default)."""
        if not account_id:
            return self.tracker
        return self._trackers.get(account_id, self.tracker)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        await self.tracker.recover()

        symbols = await self.store.list_symbol_configs()

        # Initialize per-account clients for symbols that need a different account
        for sym in symbols:
            account_id = sym.get("account_id", "")
            if account_id and account_id not in self._clients:
                try:
                    acct_client = RestClient(account_id=account_id)
                    await acct_client.start()
                    self._clients[account_id] = acct_client
                    self._trackers[account_id] = PositionTracker(acct_client, self.store)
                    await self._trackers[account_id].recover()
                    logger.info(f"Started client for account {account_id}")
                except Exception as e:
                    logger.error(f"Failed to start client for account {account_id}: {e}")

        for sym in symbols:
            await self._init_epic(sym["epic"], sym.get("strategy", "scalper"), sym.get("account_id", ""))

        # Register all clients with the PctTrailer
        self._pct_trailer.register_client("", self.client)  # default account
        for acct_id, acct_client in self._clients.items():
            self._pct_trailer.register_client(acct_id, acct_client)
        await self._pct_trailer.start()

        # Initialize bias strategy client
        try:
            bias_config = await self.store.get_bias_config()
            bias_account_id = bias_config.get("account_id", "")
            if bias_account_id:
                if bias_account_id in self._clients:
                    self._bias_client = self._clients[bias_account_id]
                    self._bias_tracker = self._trackers[bias_account_id]
                else:
                    bias_client = RestClient(account_id=bias_account_id)
                    await bias_client.start()
                    self._clients[bias_account_id] = bias_client
                    self._bias_client = bias_client
                    self._bias_tracker = PositionTracker(bias_client, self.store)
                    self._trackers[bias_account_id] = self._bias_tracker
                    await self._bias_tracker.recover()
                    self._pct_trailer.register_client(bias_account_id, bias_client)
                self.bias_strategy = BiasStrategy(self.store)
                self._bias_monitor_task = asyncio.create_task(self._bias_monitor_loop())
                # Discord: prefer .env, fall back to DB
                discord_url = settings.discord_webhook_url or bias_config.get("discord_webhook_url", "")
                if discord_url:
                    self.discord.update_url(discord_url)
                    logger.info("Discord notifier configured for bias strategy")
                logger.info(f"Bias strategy initialized (account={bias_account_id})")
        except Exception as e:
            logger.error(f"Failed to initialize bias strategy: {e}")

        self._task = asyncio.create_task(self._run())

        # Start headline fetcher if configured — prefer .env, fall back to DB
        config = await self.store.get_bot_config()
        x_token = settings.x_bearer_token or config.get("x_bearer_token", "")
        if x_token:
            self.x_scraper.update_token(x_token)
            self._headline_task = asyncio.create_task(self._headline_loop())
            logger.info("X headline fetcher started")

        logger.info("Scalper loop started")

    async def stop(self) -> None:
        self._running = False
        await self._pct_trailer.stop()
        if self._bias_monitor_task:
            self._bias_monitor_task.cancel()
            try:
                await self._bias_monitor_task
            except asyncio.CancelledError:
                pass
        if self._headline_task:
            self._headline_task.cancel()
            try:
                await self._headline_task
            except asyncio.CancelledError:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Close per-account clients
        for acct_id, c in self._clients.items():
            try:
                await c.close()
            except Exception:
                pass
        self._clients.clear()
        self._trackers.clear()
        logger.info("Scalper loop stopped")

    async def _init_epic(self, epic: str, strategy: str = "scalper", account_id: str = "") -> None:
        # Asian range needs ~420 candles (7h of 1-min), scalper needs 30
        maxlen = 500 if strategy == "asian_range" else 30
        max_bars = 500 if strategy == "asian_range" else 30
        client = self._get_client(account_id)

        if epic not in self._histories:
            self._histories[epic] = CandleHistory(maxlen=maxlen)

        if strategy == "asian_range" and epic not in self._asian_range:
            self._asian_range[epic] = AsianRangeBreakout()

        if epic not in self._min_stop_distances:
            try:
                inst = await client.get_instrument(epic)
                self._min_stop_distances[epic] = inst.min_stop_distance
                logger.info(f"{epic}: min_stop_distance = {inst.min_stop_distance}")
            except Exception as e:
                logger.warning(f"Failed to get instrument info for {epic}: {e}")
                self._min_stop_distances[epic] = 6.0

        try:
            candles = await client.get_prices(epic, "MINUTE", max_bars=max_bars)
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

                if not config.get("enabled", False):
                    await asyncio.sleep(poll_interval)
                    continue

                # Manual mode auto-shutoff: disable bot if no closes in 30 min
                if config.get("manual_mode", False):
                    last_close = await self.store.get_last_close_time()
                    if last_close is not None:
                        elapsed = (datetime.now(timezone.utc) - last_close).total_seconds()
                        if elapsed > 1800:  # 30 minutes
                            logger.warning(
                                f"MANUAL MODE AUTO-SHUTOFF: no position closed in {elapsed/60:.0f} min. Disabling bot."
                            )
                            await self.store.update_bot_config(enabled=False)
                            await self.store.log_audit(
                                "SYSTEM", "MANUAL_AUTO_SHUTOFF",
                                {"minutes_since_last_close": round(elapsed / 60, 1)},
                            )
                            await asyncio.sleep(poll_interval)
                            continue

                symbols = await self.store.list_symbol_configs()
                for sym in symbols:
                    if not self._running:
                        break
                    epic = sym["epic"]
                    if not sym.get("enabled", True):
                        continue

                    strategy = sym.get("strategy", "scalper")
                    account_id = sym.get("account_id", "")

                    if epic not in self._histories:
                        await self._init_epic(epic, strategy, account_id)

                    await self._process_epic(epic, config, sym, strategy, account_id)

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

    async def _headline_loop(self) -> None:
        """Periodically fetch new X headlines (every 5 minutes)."""
        while self._running:
            try:
                new = await self.x_scraper.fetch_new_headlines()
                if new:
                    logger.info(f"Fetched {new} new X headlines")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Headline fetch error: {e}")
            await asyncio.sleep(300)  # 5 minutes

    async def _bias_monitor_loop(self) -> None:
        """Monitor bias positions for server-side closes and signal expiry (every 5s)."""
        while self._running:
            try:
                if not self._bias_client or not self._bias_tracker:
                    await asyncio.sleep(5)
                    continue

                # Get all open bias trades from DB
                open_trades = await self.store.get_open_bias_trades()
                if not open_trades:
                    await asyncio.sleep(5)
                    continue

                # Get live positions from bias account
                try:
                    live_positions = await self._bias_client.get_positions()
                except Exception as e:
                    logger.error(f"Bias monitor: failed to get positions: {e}")
                    await asyncio.sleep(5)
                    continue

                live_map = {p.deal_id: p for p in live_positions}

                for trade in open_trades:
                    deal_id = trade["deal_id"]
                    epic = trade["epic"]

                    live = live_map.get(deal_id)
                    if live is None:
                        # Position closed server-side (TP or SL hit)
                        entry_price = trade["entry_price"]
                        sl = trade.get("stop_level")
                        tp = trade.get("profit_level")

                        # Estimate PnL
                        if tp and sl:
                            direction = trade["direction"]
                            size = trade["size"]
                            # Guess based on which level was likely hit
                            if direction == "BUY":
                                tp_pnl = (tp - entry_price) * size
                                sl_pnl = (sl - entry_price) * size
                            else:
                                tp_pnl = (entry_price - tp) * size
                                sl_pnl = (entry_price - sl) * size
                            # If SL implies loss and TP implies profit, pick based on sign
                            pnl = tp_pnl if tp_pnl > 0 else sl_pnl
                        else:
                            pnl = 0.0

                        exit_reason = "SL_HIT" if pnl < 0 else "TP_HIT"
                        exit_price = (tp if exit_reason == "TP_HIT" else sl) or entry_price

                        await self.store.save_trade_close(
                            deal_id=deal_id,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason=exit_reason,
                        )
                        await self.store.log_audit(
                            epic, f"BIAS_{exit_reason}",
                            {"deal_id": deal_id, "pnl": round(pnl, 2), "strategy": "bias"},
                            pnl=pnl,
                        )
                        self._bias_expired_logged.discard(deal_id)
                        logger.info(f"BIAS_{exit_reason} {epic} deal={deal_id} pnl={pnl:.2f}")

                        # Discord notification
                        try:
                            await self.discord.notify_bias_close(epic, exit_reason, pnl, deal_id)
                        except Exception:
                            pass
                        continue

                    # Position still open — check for signal expiry
                    if deal_id not in self._bias_expired_logged:
                        bias = await self.store.get_latest_bias(epic=epic)
                        if bias is None:
                            # Signal expired (no valid bias for this epic)
                            self._bias_expired_logged.add(deal_id)
                            await self.store.log_audit(
                                epic, "BIAS_EXPIRED",
                                {"deal_id": deal_id, "note": "signal expired while position open"},
                            )
                            logger.info(f"BIAS_EXPIRED {epic} deal={deal_id}: signal no longer valid")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Bias monitor error: {e}")
            await asyncio.sleep(5)

    async def _process_epic(
        self, epic: str, config: dict, symbol_cfg: dict, strategy: str = "scalper", account_id: str = ""
    ) -> None:
        history = self._histories[epic]
        min_stop = self._min_stop_distances.get(epic, 6.0)
        client = self._get_client(account_id)
        tracker = self._get_tracker(account_id)

        try:
            # Always fetch only 3 candles per poll cycle (init loads the full history)
            raw = await client.get_prices(epic, "MINUTE", max_bars=3)
        except Exception as e:
            logger.error(f"Failed to fetch candles for {epic}: {e}")
            return
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

        # 1. Detect server-side closes (TP/SL hit) + fast reopen
        active_scalper = self.scalper if strategy == "scalper" else None
        await tracker.check_positions(
            epic=epic,
            profit_target=profit_target,
            max_positions=max_positions,
            scalper=active_scalper,
            current_candle=current_candle,
            history=history,
            config=config,
            symbol_cfg=symbol_cfg,
            min_stop_distance=min_stop,
        )

        # 2. Schedule gate: scalper only trades in safe hours
        if strategy == "scalper":
            utc_hour = datetime.now(timezone.utc).hour
            if utc_hour not in SCALPER_SAFE_HOURS:
                return  # outside trading window, skip entry check

        # 3. Check for new entry (post-loss cooldown + max positions)
        if tracker.can_open(epic, max_positions):
            signal = None

            if strategy == "asian_range":
                ar = self._asian_range.get(epic)
                if ar is None:
                    ar = AsianRangeBreakout()
                    self._asian_range[epic] = ar
                signal = ar.check_entry(
                    epic=epic,
                    current_candle=current_candle,
                    history=history,
                    num_contracts=num_contracts,
                    profit_target=profit_target,
                    stop_loss=stop_loss,
                    min_stop_distance=min_stop,
                )
            else:
                ema_on = config.get("ema_filter_on", False)
                ema_period = config.get("ema_period", 5)
                velocity_threshold = config.get("velocity_threshold", 0.15)
                manual_mode = config.get("manual_mode", False)
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
                    velocity_threshold=velocity_threshold,
                    manual_mode=manual_mode,
                )

            if signal:
                # 4. BillionBot bias gate — block entries against strong sentiment
                if config.get("bias_filter_on", False):
                    bias = await self.bias_provider.get_bias(epic)
                    if bias:
                        min_conf = config.get("bias_min_confidence", 3)
                        blocked = bias.blocks_entry(signal.direction, min_conf)
                        if blocked:
                            logger.info(
                                f"{epic}: BIAS BLOCKED {signal.direction} — "
                                f"BillionBot says {bias.direction}({bias.confidence}): {bias.catalyst[:80]}"
                            )
                            await self.store.log_audit(
                                epic,
                                "BIAS_BLOCKED",
                                {
                                    "direction": signal.direction,
                                    "bias": bias.direction,
                                    "confidence": bias.confidence,
                                    "catalyst": bias.catalyst[:200],
                                },
                            )
                            signal = None
                        else:
                            logger.debug(
                                f"{epic}: Bias OK — {bias.direction}({bias.confidence}) allows {signal.direction}"
                            )

                if signal:
                    await tracker.open_position(signal)
