from __future__ import annotations

from datetime import datetime

import asyncpg
from loguru import logger

from microprofits.config.settings import settings


class Store:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
        await self._create_tables()
        await self._migrate()
        await self._seed_defaults()
        logger.info("Database ready")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "Store not started"
        return self._pool

    # -- schema --------------------------------------------------------------

    async def _create_tables(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_config (
                    id              INT PRIMARY KEY DEFAULT 1,
                    enabled         BOOLEAN DEFAULT FALSE,
                    profit_target   DOUBLE PRECISION DEFAULT 5.0,
                    stop_loss       DOUBLE PRECISION DEFAULT 10.0,
                    max_positions   INT DEFAULT 3,
                    num_contracts   DOUBLE PRECISION DEFAULT 1.0,
                    ema_filter_on   BOOLEAN DEFAULT FALSE,
                    ema_period      INT DEFAULT 5,
                    poll_interval   DOUBLE PRECISION DEFAULT 3.0,
                    entry_cooldown  DOUBLE PRECISION DEFAULT 30.0,
                    velocity_threshold DOUBLE PRECISION DEFAULT 0.15,
                    upl_speed_threshold DOUBLE PRECISION DEFAULT 0.05,
                    safety_max_consecutive INT DEFAULT 10,
                    safety_loss_threshold DOUBLE PRECISION DEFAULT 1.0,
                    safety_cooldown DOUBLE PRECISION DEFAULT 3600.0,
                    manual_mode     BOOLEAN DEFAULT FALSE,
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS symbol_config (
                    epic            TEXT PRIMARY KEY,
                    enabled         BOOLEAN DEFAULT TRUE,
                    strategy        TEXT DEFAULT 'scalper',
                    account_id      TEXT DEFAULT '',
                    profit_target   DOUBLE PRECISION,
                    stop_loss       DOUBLE PRECISION,
                    num_contracts   DOUBLE PRECISION,
                    max_positions   INT,
                    trail_pct       DOUBLE PRECISION DEFAULT 0,
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id              BIGSERIAL PRIMARY KEY,
                    epic            TEXT NOT NULL,
                    deal_id         TEXT NOT NULL,
                    direction       TEXT NOT NULL DEFAULT 'BUY',
                    size            DOUBLE PRECISION NOT NULL,
                    entry_price     DOUBLE PRECISION NOT NULL,
                    stop_level      DOUBLE PRECISION,
                    profit_level    DOUBLE PRECISION,
                    exit_price      DOUBLE PRECISION,
                    pnl             DOUBLE PRECISION,
                    exit_reason     TEXT,
                    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    closed_at       TIMESTAMPTZ
                );

                CREATE INDEX IF NOT EXISTS idx_trades_epic_ts ON trades(epic, opened_at DESC);
                CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at DESC)
                    WHERE closed_at IS NOT NULL;

                CREATE TABLE IF NOT EXISTS audit_log (
                    id              BIGSERIAL PRIMARY KEY,
                    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    epic            TEXT NOT NULL,
                    event           TEXT NOT NULL,
                    detail          JSONB DEFAULT '{}',
                    pnl             DOUBLE PRECISION
                );

                CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_epic ON audit_log(epic, ts DESC);
            """)

    async def _migrate(self) -> None:
        """Add columns that may not exist yet (safe to run repeatedly)."""
        async with self.pool.acquire() as conn:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'bot_config'"
            )
            existing = {r["column_name"] for r in cols}
            if "entry_cooldown" not in existing:
                await conn.execute(
                    "ALTER TABLE bot_config ADD COLUMN entry_cooldown DOUBLE PRECISION DEFAULT 30.0"
                )
                logger.info("Migrated: added entry_cooldown to bot_config")
            if "velocity_threshold" not in existing:
                await conn.execute(
                    "ALTER TABLE bot_config ADD COLUMN velocity_threshold DOUBLE PRECISION DEFAULT 0.15"
                )
                logger.info("Migrated: added velocity_threshold to bot_config")
            if "upl_speed_threshold" not in existing:
                await conn.execute(
                    "ALTER TABLE bot_config ADD COLUMN upl_speed_threshold DOUBLE PRECISION DEFAULT 0.05"
                )
                logger.info("Migrated: added upl_speed_threshold to bot_config")
            if "safety_max_consecutive" not in existing:
                await conn.execute("""
                    ALTER TABLE bot_config
                    ADD COLUMN safety_max_consecutive INT DEFAULT 10,
                    ADD COLUMN safety_loss_threshold DOUBLE PRECISION DEFAULT 1.0,
                    ADD COLUMN safety_cooldown DOUBLE PRECISION DEFAULT 3600.0
                """)
                logger.info("Migrated: added safety columns to bot_config")
            if "manual_mode" not in existing:
                await conn.execute(
                    "ALTER TABLE bot_config ADD COLUMN manual_mode BOOLEAN DEFAULT FALSE"
                )
                logger.info("Migrated: added manual_mode to bot_config")

            # Migrate symbol_config: add strategy column
            sym_cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'symbol_config'"
            )
            sym_existing = {r["column_name"] for r in sym_cols}
            if "strategy" not in sym_existing:
                await conn.execute(
                    "ALTER TABLE symbol_config ADD COLUMN strategy TEXT DEFAULT 'scalper'"
                )
                logger.info("Migrated: added strategy column to symbol_config")
            if "account_id" not in sym_existing:
                await conn.execute(
                    "ALTER TABLE symbol_config ADD COLUMN account_id TEXT DEFAULT ''"
                )
                logger.info("Migrated: added account_id column to symbol_config")
            if "trail_pct" not in sym_existing:
                await conn.execute(
                    "ALTER TABLE symbol_config ADD COLUMN trail_pct DOUBLE PRECISION DEFAULT 0"
                )
                logger.info("Migrated: added trail_pct column to symbol_config")

    async def _seed_defaults(self) -> None:
        async with self.pool.acquire() as conn:
            exists = await conn.fetchval("SELECT 1 FROM bot_config WHERE id = 1")
            if not exists:
                await conn.execute("INSERT INTO bot_config (id) VALUES (1)")
                logger.info("Seeded default bot_config")

            exists = await conn.fetchval(
                "SELECT 1 FROM symbol_config WHERE epic = 'US100'"
            )
            if not exists:
                await conn.execute(
                    "INSERT INTO symbol_config (epic, enabled, strategy) VALUES ('US100', TRUE, 'scalper')"
                )
                logger.info("Seeded US100 symbol config")

            exists = await conn.fetchval(
                "SELECT 1 FROM symbol_config WHERE epic = 'GOLD'"
            )
            if not exists:
                await conn.execute(
                    "INSERT INTO symbol_config (epic, enabled, strategy, account_id) VALUES ('GOLD', TRUE, 'asian_range', '315701137306366238')"
                )
                logger.info("Seeded GOLD (XAUUSD) symbol config with asian_range account")

    # -- bot config ----------------------------------------------------------

    async def get_bot_config(self) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM bot_config WHERE id = 1")
            return dict(row) if row else {}

    async def update_bot_config(self, **fields) -> dict:
        if not fields:
            return await self.get_bot_config()
        sets = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(fields.keys()))
        vals = list(fields.values())
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE bot_config SET {sets}, updated_at = NOW() WHERE id = 1",
                *vals,
            )
        return await self.get_bot_config()

    # -- symbol config -------------------------------------------------------

    async def get_symbol_config(self, epic: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM symbol_config WHERE epic = $1", epic
            )
            return dict(row) if row else None

    async def list_symbol_configs(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM symbol_config ORDER BY epic")
            return [dict(r) for r in rows]

    async def upsert_symbol_config(self, epic: str, **fields) -> dict:
        async with self.pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM symbol_config WHERE epic = $1", epic
            )
            if not exists:
                await conn.execute(
                    "INSERT INTO symbol_config (epic) VALUES ($1)", epic
                )
            if fields:
                sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(fields.keys()))
                vals = list(fields.values())
                await conn.execute(
                    f"UPDATE symbol_config SET {sets}, updated_at = NOW() WHERE epic = $1",
                    epic,
                    *vals,
                )
        return await self.get_symbol_config(epic)  # type: ignore

    # -- trades --------------------------------------------------------------

    async def save_trade_open(
        self,
        epic: str,
        deal_id: str,
        direction: str,
        size: float,
        entry_price: float,
        stop_level: float | None,
        profit_level: float | None,
    ) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO trades (epic, deal_id, direction, size, entry_price, stop_level, profit_level)
                   VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                epic,
                deal_id,
                direction,
                size,
                entry_price,
                stop_level,
                profit_level,
            )

    async def save_trade_close(
        self,
        deal_id: str,
        exit_price: float,
        pnl: float,
        exit_reason: str,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE trades SET exit_price = $2, pnl = $3, exit_reason = $4, closed_at = NOW()
                   WHERE deal_id = $1 AND closed_at IS NULL""",
                deal_id,
                exit_price,
                pnl,
                exit_reason,
            )

    async def get_last_close_time(self) -> datetime | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT MAX(closed_at) FROM trades WHERE closed_at IS NOT NULL"
            )

    async def get_open_trades(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY opened_at"
            )
            return [dict(r) for r in rows]

    async def get_trade_history(
        self, limit: int = 50, offset: int = 0, epic: str | None = None
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            if epic:
                rows = await conn.fetch(
                    """SELECT * FROM trades WHERE closed_at IS NOT NULL AND epic = $1
                       ORDER BY closed_at DESC LIMIT $2 OFFSET $3""",
                    epic,
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    """SELECT * FROM trades WHERE closed_at IS NOT NULL
                       ORDER BY closed_at DESC LIMIT $1 OFFSET $2""",
                    limit,
                    offset,
                )
            return [dict(r) for r in rows]

    async def get_trade_stats(self, epic: str | None = None) -> dict:
        async with self.pool.acquire() as conn:
            where = "WHERE closed_at IS NOT NULL"
            args: list = []
            if epic:
                where += " AND epic = $1"
                args.append(epic)

            row = await conn.fetchrow(
                f"""SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE pnl > 0) as wins,
                        COUNT(*) FILTER (WHERE pnl <= 0) as losses,
                        COALESCE(SUM(pnl), 0) as total_pnl,
                        COALESCE(AVG(pnl) FILTER (WHERE pnl > 0), 0) as avg_win,
                        COALESCE(AVG(pnl) FILTER (WHERE pnl <= 0), 0) as avg_loss,
                        COALESCE(MAX(pnl), 0) as best_trade,
                        COALESCE(MIN(pnl), 0) as worst_trade
                    FROM trades {where}""",
                *args,
            )
            d = dict(row) if row else {}
            total = d.get("total", 0)
            d["win_rate"] = (d.get("wins", 0) / total * 100) if total > 0 else 0
            return d

    # -- heatmap -------------------------------------------------------------

    async def get_heatmap(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    EXTRACT(DOW FROM closed_at) AS day_of_week,
                    EXTRACT(HOUR FROM closed_at) AS hour,
                    COUNT(*) AS trade_count,
                    COALESCE(SUM(pnl), 0) AS pnl
                FROM trades
                WHERE closed_at IS NOT NULL
                GROUP BY day_of_week, hour
                ORDER BY day_of_week, hour
            """)
            return [dict(r) for r in rows]

    # -- audit ---------------------------------------------------------------

    async def log_audit(
        self,
        epic: str,
        event: str,
        detail: dict | None = None,
        pnl: float | None = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            import json

            await conn.execute(
                "INSERT INTO audit_log (epic, event, detail, pnl) VALUES ($1, $2, $3, $4)",
                epic,
                event,
                json.dumps(detail or {}),
                pnl,
            )

    async def get_audit_log(self, limit: int = 100, epic: str | None = None) -> list[dict]:
        async with self.pool.acquire() as conn:
            if epic:
                rows = await conn.fetch(
                    "SELECT * FROM audit_log WHERE epic = $1 ORDER BY ts DESC LIMIT $2",
                    epic,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM audit_log ORDER BY ts DESC LIMIT $1", limit
                )
            return [dict(r) for r in rows]
