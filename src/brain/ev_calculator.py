"""Expected value calculator for Polymarket prediction market trades.

Provides pure functions to evaluate whether a trade has positive expected
value after accounting for Polymarket's dynamic taker fee structure.
"""


def calculate_ev(
    p_true: float,
    market_price: float,
    side: str,
    fee_rate: float,
) -> float:
    """Calculate expected value per dollar for a prediction market trade.

    Args:
        p_true: Estimated true probability of the outcome (0-1).
        market_price: Current market price / implied probability (0-1).
        side: Trade direction, either ``"BUY_YES"`` or ``"BUY_NO"``.
        fee_rate: Polymarket taker fee rate (e.g. 0.018 for 1.8%).

    Returns:
        EV per dollar wagered. Positive means profitable in expectation.

    The effective fee uses Polymarket's dynamic formula that peaks at 50%
    probability: ``fee_rate * market_price * (1 - market_price)``.
    """
    effective_fee = fee_rate * market_price * (1.0 - market_price)

    if side == "BUY_YES":
        return p_true - market_price - effective_fee

    # BUY_NO: edge is on the complement side
    return (1.0 - p_true) - (1.0 - market_price) - effective_fee


def determine_side(p_true: float, market_price: float) -> str:
    """Determine whether to buy YES or NO based on probability estimate.

    Args:
        p_true: Estimated true probability of the outcome (0-1).
        market_price: Current market price / implied probability (0-1).

    Returns:
        ``"BUY_YES"`` if YES is underpriced, ``"BUY_NO"`` if NO is
        underpriced, or ``"HOLD"`` if prices are within 0.001 tolerance.
    """
    diff = p_true - market_price

    if abs(diff) <= 0.001:
        return "HOLD"
    if diff > 0:
        return "BUY_YES"
    return "BUY_NO"


def should_trade(ev: float, min_ev_threshold: float = 0.05) -> bool:
    """Decide whether the expected value justifies placing a trade.

    Args:
        ev: Calculated expected value per dollar.
        min_ev_threshold: Minimum EV required to enter a position.

    Returns:
        ``True`` only when EV strictly exceeds the threshold.
    """
    return ev > min_ev_threshold
