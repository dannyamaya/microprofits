from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["config"])


class BotConfigUpdate(BaseModel):
    enabled: bool | None = None
    profit_target: float | None = None
    stop_loss: float | None = None
    max_positions: int | None = None
    num_contracts: float | None = None
    ema_filter_on: bool | None = None
    ema_period: int | None = None
    poll_interval: float | None = None
    entry_cooldown: float | None = None
    velocity_threshold: float | None = None
    upl_speed_threshold: float | None = None
    safety_max_consecutive: int | None = None
    safety_loss_threshold: float | None = None
    safety_cooldown: float | None = None
    manual_mode: bool | None = None
    bias_filter_on: bool | None = None
    bias_min_confidence: int | None = None
    # x_bearer_token removed — use .env X_BEARER_TOKEN instead


class SymbolConfigUpdate(BaseModel):
    enabled: bool | None = None
    strategy: str | None = None
    account_id: str | None = None
    profit_target: float | None = None
    stop_loss: float | None = None
    num_contracts: float | None = None
    max_positions: int | None = None
    trail_pct: float | None = None


@router.get("/config")
async def get_config(request: Request):
    store = request.app.state.store
    config = await store.get_bot_config()
    # Redact secrets from API response
    config.pop("x_bearer_token", None)
    return config


@router.put("/config")
async def update_config(request: Request, body: BotConfigUpdate):
    store = request.app.state.store
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        old = await store.get_bot_config()
        result = await store.update_bot_config(**fields)
        # Audit config changes
        for k, v in fields.items():
            old_val = old.get(k)
            if old_val != v:
                await store.log_audit(
                    "SYSTEM", "CONFIG_CHANGE",
                    {"field": k, "old": old_val, "new": v},
                )
        return result
    return await store.get_bot_config()


@router.get("/config/symbols")
async def list_symbols(request: Request):
    store = request.app.state.store
    return await store.list_symbol_configs()


@router.get("/config/symbols/{epic}")
async def get_symbol(request: Request, epic: str):
    store = request.app.state.store
    cfg = await store.get_symbol_config(epic)
    if cfg is None:
        return {"error": "not found"}, 404
    return cfg


@router.put("/config/symbols/{epic}")
async def update_symbol(request: Request, epic: str, body: SymbolConfigUpdate):
    store = request.app.state.store
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    result = await store.upsert_symbol_config(epic, **fields)
    await store.log_audit(
        epic, "SYMBOL_CONFIG_CHANGE", {"fields": fields}
    )
    return result
