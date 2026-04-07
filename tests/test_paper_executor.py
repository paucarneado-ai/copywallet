"""Tests for the paper (simulated) executor.

Uses in-memory databases for speed and isolation.  Each test gets a fresh
``Database`` + ``PaperExecutor`` pair so state never leaks between cases.
"""

import pytest
import pytest_asyncio

from src.copytrade.models import CopySignal
from src.executor.paper import PaperExecutor
from src.state.database import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> Database:
    """Yield a connected in-memory database, then close it."""
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


def _make_signal(
    position_size_usd: float = 100.0,
    current_price: float = 0.55,
    category: str = "crypto",
) -> CopySignal:
    """Build a minimal CopySignal for testing."""
    return CopySignal(
        market_id="mkt_test",
        token_id="tok_test",
        question="Will X happen?",
        category=category,
        side="BUY",
        leader_address="0xleader",
        leader_username="leader_1",
        leader_price=0.52,
        current_price=current_price,
        estimated_slippage=0.01,
        position_size_usd=position_size_usd,
        leader_category_wr=0.65,
        kelly_fraction=0.22,
        neg_risk=False,
        reasoning="Test signal",
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestPaperExecutorOpensPosition:
    """Verify that a valid signal is filled and state is updated."""

    @pytest.mark.asyncio
    async def test_paper_executor_opens_position(self, db: Database) -> None:
        """Execute a signal and verify fill status, balance decrease, and DB position."""
        executor = PaperExecutor(db, starting_bankroll=10_000)
        signal = _make_signal(position_size_usd=100.0)

        result = await executor.execute(signal)

        assert result.status == "filled"
        assert result.size_usd == 100.0
        assert result.market_id == "mkt_test"
        assert result.side == "BUY"
        assert result.order_id  # non-empty UUID string

        # Cash must have decreased by size_usd + fee
        expected_cash = 10_000 - 100.0 - result.fee
        assert await executor.get_balance() == pytest.approx(expected_cash)

        # Exactly one position stored in the database
        positions = await db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["market_id"] == "mkt_test"


class TestPaperExecutorAppliesFees:
    """Verify the fee formula: fee_rate * size_usd * p * (1 - p)."""

    @pytest.mark.asyncio
    async def test_paper_executor_applies_fees(self, db: Database) -> None:
        """Fee for a crypto signal at price 0.50 with rate 0.018 must be 0.45."""
        executor = PaperExecutor(
            db,
            starting_bankroll=10_000,
            fees={"crypto": 0.018},
        )
        signal = _make_signal(
            position_size_usd=100.0,
            current_price=0.50,
            category="crypto",
        )

        result = await executor.execute(signal)

        # fee = 100 * 0.018 * 0.50 * 0.50 = 0.45
        assert result.fee == pytest.approx(0.45)
        assert result.fee > 0


class TestPaperExecutorRejectsInsufficientBalance:
    """Verify that signals too large for the bankroll are cancelled."""

    @pytest.mark.asyncio
    async def test_paper_executor_rejects_insufficient_balance(
        self, db: Database
    ) -> None:
        """Attempting to open a position larger than cash must return cancelled."""
        executor = PaperExecutor(db, starting_bankroll=50)
        signal = _make_signal(position_size_usd=100.0)

        result = await executor.execute(signal)

        assert result.status == "cancelled"
        # Balance unchanged
        assert await executor.get_balance() == pytest.approx(50.0)
        # No position created
        positions = await db.get_open_positions()
        assert len(positions) == 0
