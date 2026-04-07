"""Tests for EV calculator with fee-adjusted edge and side determination."""

from src.brain.ev_calculator import calculate_ev, determine_side, should_trade


class TestEvBuyYesPositiveEdge:
    """BUY_YES with clear positive edge should yield EV > 0.15."""

    def test_ev_buy_yes_positive_edge(self) -> None:
        """p=0.65, market=0.48, fee=0.018.

        effective_fee = 0.018 * 0.48 * 0.52 = 0.004493
        ev = 0.65 - 0.48 - 0.004493 = 0.1655
        """
        ev = calculate_ev(0.65, 0.48, "BUY_YES", 0.018)
        assert ev > 0.15


class TestEvBuyNoPositiveEdge:
    """BUY_NO with clear positive edge should yield EV > 0.20."""

    def test_ev_buy_no_positive_edge(self) -> None:
        """p=0.30, market=0.55, fee=0.018.

        For BUY_NO: ev = (1-0.30) - (1-0.55) - fee
        ev = 0.70 - 0.45 - 0.004455 = 0.2455
        """
        ev = calculate_ev(0.30, 0.55, "BUY_NO", 0.018)
        assert ev > 0.20


class TestEvNoEdge:
    """When p equals market price, EV should be negative (fee drag only)."""

    def test_ev_no_edge(self) -> None:
        """p=0.50, market=0.50 -> ev is negative (just the fee)."""
        ev = calculate_ev(0.50, 0.50, "BUY_YES", 0.018)
        assert ev < 0


class TestEvFeeReducesEdge:
    """Higher fees should produce lower EV for the same edge."""

    def test_ev_fee_reduces_edge(self) -> None:
        """Same edge (p=0.60, market=0.48), low fee vs high fee."""
        ev_low_fee = calculate_ev(0.60, 0.48, "BUY_YES", 0.005)
        ev_high_fee = calculate_ev(0.60, 0.48, "BUY_YES", 0.05)
        assert ev_low_fee > ev_high_fee


class TestDetermineSideBuyYes:
    """When p_true > market_price, side should be BUY_YES."""

    def test_determine_side_buy_yes(self) -> None:
        """p=0.65 > market=0.48 -> YES is underpriced."""
        assert determine_side(0.65, 0.48) == "BUY_YES"


class TestDetermineSideBuyNo:
    """When p_true < market_price, side should be BUY_NO."""

    def test_determine_side_buy_no(self) -> None:
        """p=0.30 < market=0.55 -> NO is underpriced."""
        assert determine_side(0.30, 0.55) == "BUY_NO"


class TestShouldTradeAboveThreshold:
    """Trade when EV exceeds the minimum threshold."""

    def test_should_trade_above_threshold(self) -> None:
        """ev=0.08 > threshold=0.05 -> trade."""
        assert should_trade(0.08, 0.05) is True


class TestShouldTradeBelowThreshold:
    """Do not trade when EV is below the minimum threshold."""

    def test_should_trade_below_threshold(self) -> None:
        """ev=0.03 < threshold=0.05 -> no trade."""
        assert should_trade(0.03, 0.05) is False
