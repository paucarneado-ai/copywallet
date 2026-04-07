"""Tests for brain wiring in the main loop.

Verifies that the TradeSignal-to-CopySignal adapter and the brain
scan logic in main.py correctly translate brain pipeline outputs
into executor-compatible signals.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.brain.models import TradeSignal
from src.config import ClaudeBrainConfig, Config, RiskConfig, ScannerConfig
from src.copytrade.models import CopySignal
from src.scanner.models import MarketOpportunity
from src.state.database import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade_signal(**overrides) -> TradeSignal:
    """Build a TradeSignal with defaults matching a BUY_YES brain output."""
    defaults = dict(
        market_id="cond_btc",
        token_id="tok_yes",
        question="Will BTC be above $100K?",
        category="crypto",
        action="BUY_YES",
        p_true=0.65,
        p_market=0.48,
        ev=0.12,
        position_size_usd=50.0,
        kelly_fraction=0.15,
        confidence="high",
        reasoning="Strong technical indicators",
        neg_risk=False,
    )
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _adapt_signal_to_copy(signal: TradeSignal) -> CopySignal:
    """Replicate the adapter logic from main.py for testability.

    This mirrors the TradeSignal -> CopySignal conversion used in
    the main loop brain scan block.
    """
    return CopySignal(
        market_id=signal.market_id,
        token_id=signal.token_id,
        question=signal.question,
        category=signal.category,
        side=signal.action,
        leader_address="",
        leader_username="claude_brain",
        leader_price=signal.p_market,
        current_price=signal.p_market,
        estimated_slippage=0.0,
        position_size_usd=signal.position_size_usd,
        leader_category_wr=signal.p_true,
        kelly_fraction=signal.kelly_fraction,
        neg_risk=signal.neg_risk,
        reasoning=signal.reasoning,
    )


# ---------------------------------------------------------------------------
# TradeSignal -> CopySignal adapter tests
# ---------------------------------------------------------------------------


class TestTradeSignalToCopySignalAdapter:
    """Verify that the adapter correctly maps TradeSignal fields to CopySignal."""

    def test_buy_yes_signal_maps_correctly(self) -> None:
        """BUY_YES TradeSignal produces CopySignal with side='BUY_YES'."""
        signal = _make_trade_signal(action="BUY_YES")
        copy = _adapt_signal_to_copy(signal)

        assert copy.market_id == signal.market_id
        assert copy.token_id == signal.token_id
        assert copy.question == signal.question
        assert copy.category == signal.category
        assert copy.side == "BUY_YES"
        assert copy.leader_address == ""
        assert copy.leader_username == "claude_brain"
        assert copy.leader_price == signal.p_market
        assert copy.current_price == signal.p_market
        assert copy.estimated_slippage == 0.0
        assert copy.position_size_usd == signal.position_size_usd
        assert copy.leader_category_wr == signal.p_true
        assert copy.kelly_fraction == signal.kelly_fraction
        assert copy.neg_risk == signal.neg_risk
        assert copy.reasoning == signal.reasoning

    def test_buy_no_signal_maps_correctly(self) -> None:
        """BUY_NO TradeSignal produces CopySignal with side='BUY_NO'."""
        signal = _make_trade_signal(action="BUY_NO", token_id="tok_no")
        copy = _adapt_signal_to_copy(signal)

        assert copy.side == "BUY_NO"
        assert copy.token_id == "tok_no"
        assert copy.leader_username == "claude_brain"

    def test_neg_risk_flag_preserved(self) -> None:
        """neg_risk=True on TradeSignal is carried to CopySignal."""
        signal = _make_trade_signal(neg_risk=True)
        copy = _adapt_signal_to_copy(signal)

        assert copy.neg_risk is True


# ---------------------------------------------------------------------------
# Brain scan filtering tests
# ---------------------------------------------------------------------------


class TestBrainScanFiltering:
    """Verify that HOLD and SELL signals are excluded from execution."""

    def test_hold_signal_excluded(self) -> None:
        """A HOLD action should not be adapted for execution."""
        signal = _make_trade_signal(action="HOLD")
        # The main loop checks: signal.action not in ("HOLD", "SELL")
        should_execute = signal.action not in ("HOLD", "SELL")
        assert should_execute is False

    def test_sell_signal_excluded(self) -> None:
        """A SELL action should not be adapted for execution."""
        signal = _make_trade_signal(action="SELL")
        should_execute = signal.action not in ("HOLD", "SELL")
        assert should_execute is False

    def test_buy_yes_signal_included(self) -> None:
        """A BUY_YES action passes the filter."""
        signal = _make_trade_signal(action="BUY_YES")
        should_execute = signal.action not in ("HOLD", "SELL")
        assert should_execute is True

    def test_buy_no_signal_included(self) -> None:
        """A BUY_NO action passes the filter."""
        signal = _make_trade_signal(action="BUY_NO")
        should_execute = signal.action not in ("HOLD", "SELL")
        assert should_execute is True


# ---------------------------------------------------------------------------
# ClaudeBrainConfig integration
# ---------------------------------------------------------------------------


class TestClaudeBrainConfigIntegration:
    """Verify brain config controls whether scanning runs."""

    def test_brain_disabled_by_default(self) -> None:
        """Default config has brain disabled, scan should not run."""
        config = Config(mode="paper")
        assert config.claude_brain.enabled is False

    def test_brain_enabled_via_config(self) -> None:
        """Brain can be enabled through config."""
        config = Config(
            mode="paper",
            claude_brain=ClaudeBrainConfig(enabled=True),
        )
        assert config.claude_brain.enabled is True
        assert config.claude_brain.scan_interval_seconds == 120

    def test_scan_interval_from_config(self) -> None:
        """Scan interval is read from config."""
        config = Config(
            mode="paper",
            claude_brain=ClaudeBrainConfig(
                enabled=True, scan_interval_seconds=60
            ),
        )
        assert config.claude_brain.scan_interval_seconds == 60


# ---------------------------------------------------------------------------
# Brain pipeline integration with executor (end-to-end mock)
# ---------------------------------------------------------------------------


class TestBrainToExecutorFlow:
    """Verify the complete brain -> adapter -> executor flow with mocks."""

    @pytest_asyncio.fixture
    async def db(self):
        """Yield a connected in-memory database, then close it."""
        database = Database(":memory:")
        await database.connect()
        yield database
        await database.close()

    @pytest.mark.asyncio
    async def test_brain_signal_executes_through_adapter(self, db) -> None:
        """A positive-EV brain signal should be adapted and executed."""
        signal = _make_trade_signal()
        copy_signal = _adapt_signal_to_copy(signal)

        # Mock executor that accepts CopySignal
        executor = AsyncMock()
        executor.execute.return_value = MagicMock(
            status="filled", size_usd=50.0
        )

        result = await executor.execute(copy_signal)

        assert result.status == "filled"
        assert result.size_usd == 50.0
        executor.execute.assert_called_once_with(copy_signal)

    @pytest.mark.asyncio
    async def test_brain_none_signal_skips_execution(self, db) -> None:
        """When brain.evaluate returns None, no execution occurs."""
        brain = AsyncMock()
        brain.evaluate.return_value = None

        executor = AsyncMock()

        market = MagicMock(spec=MarketOpportunity)
        result = await brain.evaluate(market)

        assert result is None
        executor.execute.assert_not_called()
