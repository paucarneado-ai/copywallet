"""Tests for Kelly criterion position sizing with safety caps."""

from src.executor.kelly import kelly_size


class TestKellyBasicEdge:
    """Verify Kelly sizing returns sensible position for a clear edge."""

    def test_kelly_basic_edge(self) -> None:
        """p=0.62, market=0.48, bankroll=10000, quarter kelly -> ~471.

        edge=0.14, full_kelly=0.2692, fractional=0.0673, available=7000,
        position=471.15, capped at max_position=500 -> 471.15.
        """
        result = kelly_size(p_true=0.62, market_price=0.48, bankroll=10000)
        assert 450 <= result <= 500, f"Expected 450-500, got {result}"


class TestKellyNoEdge:
    """Verify Kelly returns zero when there is no positive edge."""

    def test_kelly_no_edge(self) -> None:
        """p=0.48 vs market=0.50 -> negative edge -> 0."""
        result = kelly_size(p_true=0.48, market_price=0.50, bankroll=10000)
        assert result == 0.0


class TestKellyRespectsMaxPosition:
    """Verify Kelly caps at max_position_pct regardless of edge size."""

    def test_kelly_respects_max_position(self) -> None:
        """Huge edge should be capped at 5% of 10000 = 500."""
        result = kelly_size(
            p_true=0.99, market_price=0.10, bankroll=10000,
        )
        assert result <= 500.0, f"Expected <= 500, got {result}"
        assert result > 0.0


class TestKellyRespectsMinTrade:
    """Verify Kelly returns 0 or >= min_trade_size, never in between."""

    def test_kelly_respects_min_trade(self) -> None:
        """Tiny edge on small bankroll -> returns 0 (below min_trade_size)."""
        result = kelly_size(
            p_true=0.51, market_price=0.50, bankroll=100,
        )
        assert result == 0.0 or result >= 10.0, (
            f"Expected 0 or >= 10, got {result}"
        )


class TestKellyRespectsCashReserve:
    """Verify Kelly only sizes from the non-reserve portion of bankroll."""

    def test_kelly_respects_cash_reserve(self) -> None:
        """bankroll=1000, reserve=30% -> available=700 -> result <= 700."""
        result = kelly_size(
            p_true=0.80, market_price=0.40, bankroll=1000,
        )
        assert result <= 700.0, f"Expected <= 700, got {result}"


class TestKellyConservativeFraction:
    """Verify a smaller kelly_fraction produces a smaller position."""

    def test_kelly_copy_signal_uses_conservative_fraction(self) -> None:
        """kelly_fraction=0.175 should produce smaller size than 0.25."""
        conservative = kelly_size(
            p_true=0.62, market_price=0.48, bankroll=10000,
            kelly_fraction=0.175,
        )
        standard = kelly_size(
            p_true=0.62, market_price=0.48, bankroll=10000,
            kelly_fraction=0.25,
        )
        assert conservative < standard, (
            f"Conservative {conservative} should be < standard {standard}"
        )
