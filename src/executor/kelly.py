"""Kelly criterion position sizing with fractional scaling and safety caps.

Uses quarter-Kelly by default to balance growth rate against drawdown risk.
All monetary values are in USD.
"""


def kelly_size(
    p_true: float,
    market_price: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.05,
    cash_reserve_pct: float = 0.30,
    min_trade_size: float = 10.0,
) -> float:
    """Compute position size using fractional Kelly criterion.

    Args:
        p_true: Estimated true probability of the outcome.
        market_price: Current market price (implied probability).
        bankroll: Total bankroll in USD.
        kelly_fraction: Fraction of full Kelly to use (default quarter).
        max_position_pct: Maximum position as fraction of bankroll.
        cash_reserve_pct: Fraction of bankroll held as cash reserve.
        min_trade_size: Minimum trade size in USD; smaller sizes return 0.

    Returns:
        Position size in USD, or 0.0 if no edge or below minimum.
    """
    edge = p_true - market_price
    if edge <= 0:
        return 0.0

    full_kelly = edge / (1.0 - market_price)
    fractional = full_kelly * kelly_fraction

    available = bankroll * (1.0 - cash_reserve_pct)
    max_position = bankroll * max_position_pct

    position = fractional * available
    position = min(position, max_position)

    if position < min_trade_size:
        return 0.0

    return round(position, 2)
