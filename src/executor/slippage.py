"""Orderbook walk slippage estimator for Polymarket CLOB orders.

Walks the orderbook levels to estimate the weighted average fill price
for a given order size, then calculates slippage relative to a leader's
observed price.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def estimate_fill_price(
    book: Dict[str, List[Dict[str, str]]],
    side: str,
    size_usd: float,
) -> Optional[float]:
    """Walk the orderbook to estimate weighted average fill price.

    Args:
        book: Orderbook dict with ``"asks"`` and ``"bids"`` keys.
              Each side is a list of ``{"price": str, "size": str}``
              as returned by the Polymarket CLOB API.
        side: ``"BUY"`` (walks asks) or ``"SELL"`` (walks bids).
        size_usd: Total USD notional to fill.

    Returns:
        Weighted average fill price, or ``None`` if the book has
        insufficient liquidity to fill the entire order.
    """
    levels = book["asks"] if side == "BUY" else book["bids"]

    remaining_usd = size_usd
    total_shares = 0.0
    total_cost = 0.0

    for level in levels:
        price = float(level["price"])
        available_shares = float(level["size"])

        # How many shares can we afford at this price level?
        affordable_shares = remaining_usd / price
        shares_at_level = min(affordable_shares, available_shares)

        cost_at_level = shares_at_level * price
        total_shares += shares_at_level
        total_cost += cost_at_level
        remaining_usd -= cost_at_level

        if remaining_usd <= 1e-9:
            break

    if remaining_usd > 1e-9:
        return None

    return total_cost / total_shares


def calculate_slippage(leader_price: float, fill_price: float) -> float:
    """Calculate slippage as percentage difference from leader's price.

    Args:
        leader_price: The price at which the leader executed.
        fill_price: The estimated fill price from the orderbook walk.

    Returns:
        Absolute percentage difference (e.g. 0.04 for 4% slippage).
    """
    if leader_price == 0:
        return 0.0
    return abs(fill_price - leader_price) / leader_price
