"""Nine-stage copy trading filter pipeline.

Each filter evaluates one aspect of a trading signal and rejects it
with a descriptive reason when the signal fails to meet the configured
threshold.  Filters run in a fixed order; the first failure short-circuits
the pipeline.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List

from src.config import CopyTradingConfig

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Outcome of the filter pipeline.

    Attributes:
        passed: ``True`` when every filter accepted the signal.
        reason: Empty on success; a human-readable rejection reason on failure.
    """

    passed: bool
    reason: str = ""


# ---------------------------------------------------------------------------
# Individual filter functions
# ---------------------------------------------------------------------------

def _check_win_rate(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject leaders whose category win rate is outside the acceptable band."""
    wr = signal["leader_category_wr"]
    if wr < config.min_win_rate:
        return FilterResult(False, f"Win rate {wr:.2f} below minimum {config.min_win_rate:.2f}")
    if wr > config.max_win_rate:
        return FilterResult(False, f"Win rate {wr:.2f} above maximum {config.max_win_rate:.2f}")
    return FilterResult(True)


def _check_category_history(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject leaders with insufficient category trading history."""
    positions = signal["leader_category_positions"]
    if positions < config.min_category_positions:
        return FilterResult(
            False,
            f"Category history {positions} below minimum {config.min_category_positions}",
        )
    return FilterResult(True)


def _check_staleness(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject signals that are too stale to copy profitably."""
    age = signal["trade_age_seconds"]
    if age > config.max_trade_age_seconds:
        return FilterResult(False, f"Stale trade: age {age}s exceeds maximum {config.max_trade_age_seconds}s")
    return FilterResult(True)


def _check_direction(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject signals where the leader is reducing (selling) a position."""
    if signal["leader_is_reducing"]:
        return FilterResult(False, "Leader is reducing position; skipping sell signal")
    return FilterResult(True)


def _check_price_drift(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject signals where the price has drifted too far since the leader traded."""
    drift = signal["price_drift"]
    if drift > config.max_price_drift:
        return FilterResult(False, f"Price drift {drift:.4f} exceeds maximum {config.max_price_drift:.4f}")
    return FilterResult(True)


def _check_spread(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject markets with a spread wider than the configured threshold."""
    spread = signal["spread"]
    if spread > config.max_spread:
        return FilterResult(False, f"Spread {spread:.4f} exceeds maximum {config.max_spread:.4f}")
    return FilterResult(True)


def _check_slippage(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject orders where estimated slippage is unacceptable."""
    slippage = signal["estimated_slippage"]
    if slippage > config.max_slippage:
        return FilterResult(False, f"Slippage {slippage:.4f} exceeds maximum {config.max_slippage:.4f}")
    return FilterResult(True)


def _check_exposure(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject when the portfolio already has the maximum number of open positions."""
    count = signal["current_positions_count"]
    if count >= config.max_concurrent_positions:
        return FilterResult(
            False,
            f"Exposure limit: {count} positions meets or exceeds maximum {config.max_concurrent_positions}",
        )
    return FilterResult(True)


def _check_duplicate(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Reject when we already hold a position in the same market."""
    if signal["market_has_position"]:
        return FilterResult(False, "Duplicate market: already holding a position")
    return FilterResult(True)


# Ordered pipeline -- first failure short-circuits.
_FILTER_PIPELINE: List[Callable[[Dict, CopyTradingConfig], FilterResult]] = [
    _check_win_rate,
    _check_category_history,
    _check_staleness,
    _check_direction,
    _check_price_drift,
    _check_spread,
    _check_slippage,
    _check_exposure,
    _check_duplicate,
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_filters(signal: Dict, config: CopyTradingConfig) -> FilterResult:
    """Run the 9-stage filter pipeline against a copy trading signal.

    Filters execute in a fixed order.  The first filter that rejects the
    signal short-circuits the pipeline and returns immediately with the
    rejection reason.

    Args:
        signal: Dictionary containing signal fields (win rate, age, drift, etc.).
        config: Copy trading configuration with threshold values.

    Returns:
        ``FilterResult(passed=True)`` when all filters accept, otherwise
        ``FilterResult(passed=False, reason=...)`` with the first rejection.
    """
    for check in _FILTER_PIPELINE:
        result = check(signal, config)
        if not result.passed:
            logger.debug("Signal rejected: %s", result.reason)
            return result

    return FilterResult(passed=True)
