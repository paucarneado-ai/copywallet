"""Tests for the 9-stage copy trading filter pipeline."""

from src.config import CopyTradingConfig
from src.copytrade.filters import FilterResult, apply_filters


def _default_config() -> CopyTradingConfig:
    """Return a CopyTradingConfig with sensible test defaults."""
    return CopyTradingConfig()


def _make_signal(**overrides) -> dict:
    """Build a signal dict that passes all filters by default."""
    base = {
        "category": "crypto",
        "leader_category_wr": 0.62,
        "leader_category_positions": 30,
        "trade_age_seconds": 5,
        "leader_is_reducing": False,
        "price_drift": 0.01,
        "spread": 0.04,
        "estimated_slippage": 0.01,
        "current_positions_count": 3,
        "market_has_position": False,
    }
    base.update(overrides)
    return base


class TestApplyFilters:
    """Verify each filter stage rejects bad signals and passes good ones."""

    def test_filter_passes_good_signal(self) -> None:
        """All defaults pass every filter."""
        result = apply_filters(_make_signal(), _default_config())
        assert result.passed is True
        assert result.reason == ""

    def test_filter_rejects_wrong_category(self) -> None:
        """Low win rate (below min_win_rate) is rejected."""
        result = apply_filters(
            _make_signal(leader_category_wr=0.40),
            _default_config(),
        )
        assert result.passed is False
        assert "win rate" in result.reason.lower()

    def test_filter_rejects_stale_trade(self) -> None:
        """Trade older than max_trade_age_seconds is rejected."""
        result = apply_filters(
            _make_signal(trade_age_seconds=120),
            _default_config(),
        )
        assert result.passed is False
        assert "stale" in result.reason.lower() or "age" in result.reason.lower()

    def test_filter_rejects_reducing_position(self) -> None:
        """Leader reducing (selling) a position is rejected."""
        result = apply_filters(
            _make_signal(leader_is_reducing=True),
            _default_config(),
        )
        assert result.passed is False
        assert "reduc" in result.reason.lower()

    def test_filter_rejects_price_drift(self) -> None:
        """Excessive price drift is rejected."""
        result = apply_filters(
            _make_signal(price_drift=0.08),
            _default_config(),
        )
        assert result.passed is False
        assert "drift" in result.reason.lower()

    def test_filter_rejects_wide_spread(self) -> None:
        """Spread wider than max_spread is rejected."""
        result = apply_filters(
            _make_signal(spread=0.15),
            _default_config(),
        )
        assert result.passed is False
        assert "spread" in result.reason.lower()

    def test_filter_rejects_high_slippage(self) -> None:
        """Estimated slippage above max_slippage is rejected."""
        result = apply_filters(
            _make_signal(estimated_slippage=0.05),
            _default_config(),
        )
        assert result.passed is False
        assert "slippage" in result.reason.lower()

    def test_filter_rejects_at_max_positions(self) -> None:
        """At max concurrent positions, new trades are rejected."""
        config = _default_config()  # max_concurrent_positions = 10
        result = apply_filters(
            _make_signal(current_positions_count=5),
            CopyTradingConfig(max_concurrent_positions=5),
        )
        assert result.passed is False
        assert "position" in result.reason.lower() or "exposure" in result.reason.lower()

    def test_filter_rejects_duplicate_market(self) -> None:
        """Already holding a position in the same market is rejected."""
        result = apply_filters(
            _make_signal(market_has_position=True),
            _default_config(),
        )
        assert result.passed is False
        assert "duplicate" in result.reason.lower()

    def test_filter_rejects_insufficient_category_history(self) -> None:
        """Too few category positions disqualifies the leader."""
        result = apply_filters(
            _make_signal(leader_category_positions=5),
            _default_config(),
        )
        assert result.passed is False
        assert "category" in result.reason.lower() or "history" in result.reason.lower()
