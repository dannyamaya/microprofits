from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Session:
    cst: str
    security_token: str


@dataclass
class Account:
    account_id: str
    balance: float
    deposit: float
    profit_loss: float
    available: float


@dataclass
class Instrument:
    epic: str
    name: str
    min_deal_size: float
    max_deal_size: float
    margin_factor: float
    min_stop_distance: float
    currency: str = "USD"


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


@dataclass
class Position:
    deal_id: str
    epic: str
    direction: str
    size: float
    open_level: float
    stop_level: float | None
    profit_level: float | None
    upl: float
    currency: str = "USD"


@dataclass
class OrderConfirmation:
    deal_id: str
    deal_reference: str
    status: str
    reason: str
    level: float
    size: float
    direction: str
