"""
Bias Strategy

Event-driven strategy that opens positions based on BillionBot sentiment signals.
When a bias signal arrives via POST /api/bias with sufficient confidence,
opens a position immediately at market.

Key features:
- OIL_CRUDE (and any inverted instrument) flips direction
- One position per signal per epic
- Pre-flight margin check before opening
- Falls back to PctTrailer when signal lacks SL/TP
"""

from __future__ import annotations

from loguru import logger

from microprofits.api.rest_client import RestClient
from microprofits.api.exceptions import OrderError
from microprofits.data.store import Store
from microprofits.strategy.bias_provider import BiasSignal
from microprofits.strategy.scalper import EntrySignal


def resolve_direction(bias_direction: str, inverted: bool) -> str | None:
    """Map bias direction to trade direction, with optional inversion."""
    if bias_direction == "BULLISH":
        return "SELL" if inverted else "BUY"
    if bias_direction == "BEARISH":
        return "BUY" if inverted else "SELL"
    return None  # NEUTRAL


class BiasStrategy:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def process_signal(
        self,
        bias_signal: BiasSignal,
        signal_id: int,
        client: RestClient,
    ) -> dict | None:
        """
        Evaluate a bias signal and open a position if criteria are met.
        Returns trade info dict on success, None if skipped/blocked.
        """
        epic = bias_signal.epic
        if not epic:
            logger.debug(f"Bias signal has no epic, skipping: {bias_signal.instrument}")
            return None

        # 1. Load bias config
        config = await self._store.get_bias_config()
        if not config.get("enabled"):
            logger.debug("Bias strategy disabled")
            return None

        confidence_threshold = config.get("confidence_threshold", 3)
        if bias_signal.confidence < confidence_threshold:
            await self._store.log_audit(
                epic, "BIAS_BLOCKED",
                {"reason": "low_confidence", "confidence": bias_signal.confidence,
                 "threshold": confidence_threshold, "signal_id": signal_id},
            )
            logger.info(f"BIAS_BLOCKED {epic}: confidence {bias_signal.confidence} < {confidence_threshold}")
            return None

        # 2. Load instrument config
        instrument = await self._store.get_bias_instrument(epic)
        if not instrument:
            logger.debug(f"No bias instrument config for {epic}")
            return None
        if not instrument.get("enabled"):
            await self._store.log_audit(
                epic, "BIAS_BLOCKED",
                {"reason": "instrument_disabled", "signal_id": signal_id},
            )
            logger.info(f"BIAS_BLOCKED {epic}: instrument disabled")
            return None

        # 3. NEUTRAL = no trade
        direction = resolve_direction(bias_signal.direction, instrument.get("inverted", False))
        if direction is None:
            await self._store.log_audit(
                epic, "BIAS_NEUTRAL_SKIP",
                {"signal_id": signal_id, "instrument": bias_signal.instrument},
            )
            logger.info(f"BIAS_NEUTRAL_SKIP {epic}")
            return None

        # 4. One position per epic
        open_trades = await self._store.get_open_bias_trades(epic)
        if open_trades:
            existing_dir = open_trades[0]["direction"]
            if existing_dir == direction:
                logger.info(f"BIAS_SKIP {epic}: already positioned {direction}")
                return None
            # Contradicting — HOLD
            await self._store.log_audit(
                epic, "BIAS_CONTRA_HOLD",
                {"signal_id": signal_id, "existing": existing_dir,
                 "new_bias": bias_signal.direction, "would_be": direction},
            )
            logger.info(f"BIAS_CONTRA_HOLD {epic}: holding {existing_dir}, ignoring {direction}")
            return None

        # 5. Margin check
        try:
            accounts = await client.get_accounts()
            bias_account_id = config.get("account_id", "")
            available = 0.0
            for acc in accounts:
                if acc.account_id == bias_account_id:
                    available = acc.available
                    break
            if available <= 0:
                await self._store.log_audit(
                    epic, "BIAS_MARGIN_BLOCKED",
                    {"signal_id": signal_id, "available": available},
                )
                logger.warning(f"BIAS_MARGIN_BLOCKED {epic}: available={available}")
                return None
        except Exception as e:
            logger.warning(f"Margin check failed, proceeding anyway: {e}")

        # 6. Build order
        num_contracts = instrument.get("num_contracts", 1.0)
        inverted = instrument.get("inverted", False)

        # Bias signal provides levels from the bias perspective:
        #   BULLISH: TP above current price, SL below
        #   BEARISH: TP below current price, SL above
        # When inverted, we trade the opposite direction, so SL and TP swap:
        #   BULLISH + inverted = SELL → SL was below (now needs to be above) = use TP as SL
        if inverted and bias_signal.stop_loss_price and bias_signal.price_target:
            stop_level = bias_signal.price_target      # was TP (above) → now SL for SELL
            profit_level = bias_signal.stop_loss_price  # was SL (below) → now TP for SELL
        else:
            stop_level = bias_signal.stop_loss_price
            profit_level = bias_signal.price_target

        has_full_levels = stop_level is not None and profit_level is not None
        use_trail = not has_full_levels

        signal = EntrySignal(
            epic=epic,
            direction=direction,
            size=num_contracts,
            stop_level=stop_level or 0,
            profit_level=profit_level if has_full_levels else None,
            entry_price=bias_signal.current_price or 0,
        )

        # 7. Open position
        try:
            confirm = await client.open_position(
                epic=epic,
                direction=direction,
                size=num_contracts,
                stop_level=stop_level,
                profit_level=profit_level if has_full_levels else None,
            )
        except OrderError as e:
            await self._store.log_audit(
                epic, "BIAS_ENTRY_REJECTED",
                {"reason": str(e.reason), "signal_id": signal_id, "direction": direction},
            )
            logger.warning(f"BIAS_ENTRY_REJECTED {epic}: {e.reason}")
            return None
        except Exception as e:
            logger.error(f"Failed to open bias position for {epic}: {e}")
            return None

        # 8. Reconcile deal_id with live positions
        actual_deal_id = confirm.deal_id
        try:
            import asyncio
            await asyncio.sleep(1)
            live = await client.get_positions()
            for p in live:
                if p.epic == epic and p.direction == direction:
                    actual_deal_id = p.deal_id
                    break
        except Exception:
            pass

        fill = confirm.level

        # 9. Save to DB
        try:
            db_id = await self._store.save_trade_open(
                epic=epic,
                deal_id=actual_deal_id,
                direction=direction,
                size=confirm.size,
                entry_price=fill,
                stop_level=stop_level,
                profit_level=profit_level if has_full_levels else None,
                strategy="bias",
            )
        except Exception as e:
            logger.error(f"Failed to save bias trade to DB: {e}")
            db_id = None

        # 10. Audit
        inverted = instrument.get("inverted", False)
        detail = {
            "deal_id": actual_deal_id,
            "signal_id": signal_id,
            "price": fill,
            "size": confirm.size,
            "direction": direction,
            "bias": bias_signal.direction,
            "confidence": bias_signal.confidence,
            "inverted": inverted,
            "sl": stop_level,
            "tp": profit_level if has_full_levels else None,
            "use_trail": use_trail,
            "catalyst": bias_signal.catalyst,
        }
        await self._store.log_audit(epic, "BIAS_ENTRY", detail)

        logger.info(
            f"BIAS_ENTRY {direction} {epic} x{confirm.size} @ {fill} "
            f"(bias={bias_signal.direction}, conf={bias_signal.confidence}, "
            f"inverted={inverted}, sl={stop_level}, tp={profit_level})"
        )

        return {
            "action": "BIAS_ENTRY",
            "epic": epic,
            "deal_id": actual_deal_id,
            "direction": direction,
            "size": confirm.size,
            "price": fill,
            "sl": stop_level,
            "tp": profit_level if has_full_levels else None,
            "use_trail": use_trail,
            "trail_pct": instrument.get("trail_pct") or config.get("trail_pct", 70.0) if use_trail else None,
            "inverted": inverted,
            "signal_id": signal_id,
        }
