"""Data models for the Claude brain probability-scoring pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradeSignal:
    """An evaluated trade signal produced by the brain module.

    Contains Claude's probability estimate, expected value calculation,
    Kelly-optimal position sizing, and the reasoning chain behind the
    decision.

    Attributes:
        market_id: Polymarket condition_id for the market.
        token_id: CLOB token identifier for the chosen outcome.
        question: Human-readable market question text.
        category: Market category tag (e.g. "crypto", "politics").
        action: Trade action: "BUY_YES", "BUY_NO", "SELL", or "HOLD".
        p_true: Claude's estimated true probability of the YES outcome.
        p_market: Current market-implied probability (YES midpoint price).
        ev: Expected value of the trade (p_true - p_market for BUY_YES).
        position_size_usd: Dollar amount to risk on this trade.
        kelly_fraction: Fraction of bankroll per Kelly criterion.
        confidence: Qualitative confidence level: "high", "medium", or "low".
        reasoning: Free-text explanation of the probability estimate.
        neg_risk: Whether the market uses the negRisk exchange contract.
    """

    market_id: str
    token_id: str
    question: str
    category: str
    action: str
    p_true: float
    p_market: float
    ev: float
    position_size_usd: float
    kelly_fraction: float
    confidence: str
    reasoning: str
    neg_risk: bool
