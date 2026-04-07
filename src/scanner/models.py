"""Data models for market scanning opportunities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketOpportunity:
    """A filtered market opportunity discovered by the scanner.

    Represents a Polymarket market that passes all scanner filters
    (volume, spread, time-to-resolution) and is ready for evaluation
    by the brain module.

    Attributes:
        market_id: Polymarket condition_id for the market.
        token_id_yes: CLOB token identifier for the YES outcome.
        token_id_no: CLOB token identifier for the NO outcome.
        question: Human-readable market question text.
        category: Market category tag (e.g. "crypto", "politics").
        yes_price: Current midpoint price of the YES token.
        no_price: Derived price of the NO token (1 - yes_price).
        volume_24h: Rolling 24-hour trading volume in USD.
        spread: Current bid-ask spread on the YES token.
        end_date: ISO-8601 timestamp when the market resolves.
        neg_risk: Whether the market uses the negRisk exchange contract.
        is_existing_position: True if we already hold a position in this market.
    """

    market_id: str
    token_id_yes: str
    token_id_no: str
    question: str
    category: str
    yes_price: float
    no_price: float
    volume_24h: float
    spread: float
    end_date: str
    neg_risk: bool
    is_existing_position: bool
