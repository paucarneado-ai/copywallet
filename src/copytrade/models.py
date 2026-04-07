"""Data models for copy trading signal discovery and wallet profiling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class CategoryStats:
    """Aggregated trading statistics for a single market category."""

    win_rate: float
    resolved_positions: int
    roi: float
    profit_factor: float
    avg_position_size: float
    total_pnl: float


@dataclass
class WalletProfile:
    """Scored profile of a leader wallet tracked for copy trading."""

    address: str
    username: str
    categories: dict[str, CategoryStats]
    total_pnl: float
    total_volume: float
    is_suspicious: bool = False
    last_scored: datetime | None = None


@dataclass
class CopySignal:
    """Actionable copy trading signal ready for position sizing and execution."""

    market_id: str
    token_id: str
    question: str
    category: str
    side: str
    leader_address: str
    leader_username: str
    leader_price: float
    current_price: float
    estimated_slippage: float
    position_size_usd: float
    leader_category_wr: float
    kelly_fraction: float
    neg_risk: bool
    reasoning: str
