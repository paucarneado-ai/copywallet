"""Data models for order execution and position tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OrderResult:
    """Result of an order execution attempt on the CLOB."""

    order_id: str
    market_id: str
    side: str
    price: float
    size_usd: float
    size_shares: float
    fee: float
    status: str


@dataclass
class Position:
    """Active position held in a prediction market."""

    market_id: str
    token_id: str
    side: str
    entry_price: float
    size_shares: float
    size_usd: float
    signal_source: str
    leader_address: str | None = None
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    opened_at: datetime = field(default_factory=datetime.utcnow)
