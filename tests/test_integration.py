"""Full pipeline integration test: discovery -> monitor -> filters -> execute.

Wires up all real internal components against a mocked PolymarketAPI
to verify that the end-to-end copy trading flow works correctly.
Uses in-memory SQLite for speed and isolation.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from src.config import Config, CopyTradingConfig, RiskConfig
from src.copytrade.discovery import WalletDiscovery
from src.copytrade.monitor import CopyTradeMonitor
from src.executor.paper import PaperExecutor
from src.state.database import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> Config:
    """Create a config suitable for integration testing.

    Uses lowered thresholds so a small number of mock trades
    is sufficient to qualify a wallet for tracking.
    """
    return Config(
        mode="paper",
        risk=RiskConfig(
            starting_bankroll=10000,
            kelly_fraction=0.25,
            max_position_pct=0.05,
            cash_reserve_pct=0.30,
            min_trade_size=10,
        ),
        copy_trading=CopyTradingConfig(
            enabled=True,
            categories=["CRYPTO"],
            min_win_rate=0.55,
            max_win_rate=0.90,
            min_volume=1000,
            min_resolved_positions=5,
            min_category_positions=3,
            max_trade_age_seconds=60,
            max_price_drift=0.05,
            max_spread=0.10,
            max_slippage=0.03,
            max_concurrent_positions=10,
            max_tracked_wallets=15,
            max_position_per_market=100,
        ),
        fees={"crypto": 0.018, "default": 0.01},
    )


def _build_leader_trades(
    won: int = 7, lost: int = 3
) -> list[dict]:
    """Build resolved trade dicts that score_wallet_trades can process.

    Each trade carries a ``condition_id`` so that the discovery enrichment
    step can look up market metadata.  All trades share a single condition
    to keep the mock simple.
    """
    trades: list[dict] = []
    for i in range(won):
        trades.append({
            "condition_id": f"cond_{i}",
            "side": "BUY",
            "price": 0.45,
            "size": 50,
            "status": "WON",
            "outcome": "YES",
            "asset_id": f"tok_{i}",
        })
    for i in range(won, won + lost):
        trades.append({
            "condition_id": f"cond_{i}",
            "side": "BUY",
            "price": 0.55,
            "size": 50,
            "status": "LOST",
            "outcome": "YES",
            "asset_id": f"tok_{i}",
        })
    return trades


def _mock_api() -> AsyncMock:
    """Create a mocked PolymarketAPI that returns realistic test data.

    The mock data models a single leader wallet (``0xLeader123``) with
    a 70 % win rate across 10 resolved Crypto trades, plus one fresh
    activity trade that should pass all filters.
    """
    api = AsyncMock()

    # Leaderboard returns one leader -- field names match what
    # WalletDiscovery actually reads: address, username, volume, pnl.
    api.get_leaderboard.return_value = [
        {
            "address": "0xLeader123",
            "username": "top_trader",
            "volume": 500000,
            "pnl": 50000,
        },
    ]

    # Leader's full trade history: 7 won, 3 lost -> 70 % WR
    api.get_all_trades.return_value = _build_leader_trades(won=7, lost=3)

    # Market metadata (Gamma API) -- each condition_id resolves here.
    # The first tag label becomes the trade category.
    api.get_market.return_value = {
        "conditionId": "cond_new",
        "question": "Will BTC be above $100K?",
        "tags": [{"label": "Crypto"}],
        "negRisk": False,
    }

    # Activity: one brand-new trade from the leader
    api.get_activity.return_value = [
        {
            "id": "trade_new_1",
            "market": "cond_new",
            "asset_id": "tok_new",
            "side": "BUY",
            "price": 0.48,
            "match_time": "2099-01-01T00:00:00Z",
            "size": 100,
        },
    ]

    # CLOB pricing data
    api.get_spread.return_value = 0.03
    api.get_midpoint.return_value = 0.49

    api.get_book.return_value = {
        "asks": [
            {"price": "0.49", "size": "500"},
            {"price": "0.50", "size": "1000"},
        ],
        "bids": [
            {"price": "0.48", "size": "500"},
        ],
    }

    return api


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> Database:
    """Provide a connected in-memory database, closed after the test."""
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Happy-path: full pipeline end-to-end
# ---------------------------------------------------------------------------


class TestFullPipelineEndToEnd:
    """Verify the complete flow: discover -> monitor -> filter -> execute."""

    @pytest.mark.asyncio
    async def test_discovery_to_execution(self, db: Database) -> None:
        """Walk through every pipeline stage and assert DB state at each step.

        1. WalletDiscovery scores the leader and stores in DB
        2. CopyTradeMonitor detects the leader's new trade
        3. Filters pass (leader has 70 % WR in Crypto, trade timestamp is future)
        4. PaperExecutor fills the trade
        5. Trade and position appear in DB
        6. Cash balance decreases
        """
        config = _make_config()
        api = _mock_api()
        executor = PaperExecutor(db, config.risk.starting_bankroll, config.fees)

        # -- Step 1: Discovery --
        discovery = WalletDiscovery(api, db, config.copy_trading)
        await discovery.discover()

        wallets = await db.get_tracked_wallets()
        assert len(wallets) >= 1, "Expected at least one tracked wallet"
        assert wallets[0]["username"] == "top_trader"

        # Verify category stats are parsed correctly
        stats = wallets[0]["category_stats"]
        assert "Crypto" in stats, f"Expected 'Crypto' in stats keys: {list(stats.keys())}"
        assert stats["Crypto"]["win_rate"] == pytest.approx(0.70, rel=0.01)
        assert stats["Crypto"]["resolved_positions"] == 10

        # -- Step 2: Monitor loads the wallet list --
        monitor = CopyTradeMonitor(api, db, config)
        await monitor.refresh_wallet_list()

        # -- Step 3: Poll for new trade signals --
        signals = await monitor.poll_next()

        # The fresh trade at a future timestamp should pass all filters
        assert len(signals) >= 1, "Expected at least one signal from poll_next"
        signal = signals[0]
        assert signal.leader_address == "0xLeader123"
        assert signal.side == "BUY_YES"

        # -- Step 4: Execute the signal --
        result = await executor.execute(signal)
        assert result.status == "filled"
        assert result.size_usd > 0

        # -- Step 5: Verify DB state --
        positions = await db.get_open_positions()
        assert len(positions) >= 1, "Expected at least one open position"

        trades = await db.get_recent_trades(10)
        assert len(trades) >= 1, "Expected at least one recorded trade"
        assert trades[0]["signal_source"] == "copy"

        # -- Step 6: Verify cash decreased --
        assert executor.cash_balance < config.risk.starting_bankroll


# ---------------------------------------------------------------------------
# Rejection: suspicious wallet excluded
# ---------------------------------------------------------------------------


class TestPipelineRejectsSuspiciousWallet:
    """Verify that a wallet with inflated stats is excluded from tracking."""

    @pytest.mark.asyncio
    async def test_suspicious_wallet_not_tracked(self, db: Database) -> None:
        """A leader with 95 % WR across 20 trades (< 100) should be flagged."""
        config = _make_config()
        api = _mock_api()

        # Override: 19 wins, 1 loss -> 95 % WR (suspicious)
        api.get_all_trades.return_value = _build_leader_trades(won=19, lost=1)

        discovery = WalletDiscovery(api, db, config.copy_trading)
        await discovery.discover()

        wallets = await db.get_tracked_wallets()
        assert len(wallets) == 0, (
            "Suspicious wallet (95 % WR, < 100 positions) must be excluded"
        )


# ---------------------------------------------------------------------------
# Rejection: stale trade filtered out
# ---------------------------------------------------------------------------


class TestPipelineRejectsStaleTrade:
    """Verify that a trade older than max_trade_age_seconds is filtered out."""

    @pytest.mark.asyncio
    async def test_stale_trade_rejected(self, db: Database) -> None:
        """A trade with a very old timestamp must produce zero signals."""
        config = _make_config()
        api = _mock_api()

        # Override activity: trade timestamp is far in the past
        api.get_activity.return_value = [
            {
                "id": "trade_old",
                "market": "cond_new",
                "asset_id": "tok_new",
                "side": "BUY",
                "price": 0.48,
                "match_time": "2020-01-01T00:00:00Z",
                "size": 100,
            },
        ]

        executor = PaperExecutor(db, config.risk.starting_bankroll, config.fees)

        # Discover wallet so the monitor has someone to poll
        discovery = WalletDiscovery(api, db, config.copy_trading)
        await discovery.discover()

        monitor = CopyTradeMonitor(api, db, config)
        await monitor.refresh_wallet_list()

        signals = await monitor.poll_next()
        assert len(signals) == 0, "Stale trade should have been filtered"

        # No positions should have been opened
        positions = await db.get_open_positions()
        assert len(positions) == 0


# ---------------------------------------------------------------------------
# Rejection: low-volume wallet skipped during discovery
# ---------------------------------------------------------------------------


class TestPipelineSkipsLowVolumeWallet:
    """Verify that a wallet below min_volume is not tracked."""

    @pytest.mark.asyncio
    async def test_low_volume_wallet_skipped(self, db: Database) -> None:
        """A leader with volume below config.min_volume is never scored."""
        config = _make_config()
        api = _mock_api()

        # Override: leader has volume below the 1000 minimum
        api.get_leaderboard.return_value = [
            {
                "address": "0xSmallFish",
                "username": "low_vol_trader",
                "volume": 500,
                "pnl": 100,
            },
        ]

        discovery = WalletDiscovery(api, db, config.copy_trading)
        await discovery.discover()

        wallets = await db.get_tracked_wallets()
        assert len(wallets) == 0, "Low-volume wallet must be skipped"

        # get_all_trades should never have been called for this wallet
        api.get_all_trades.assert_not_called()


# ---------------------------------------------------------------------------
# Rejection: duplicate trade deduplication
# ---------------------------------------------------------------------------


class TestPipelineDeduplicatesTrades:
    """Verify that the same trade ID is not processed twice."""

    @pytest.mark.asyncio
    async def test_second_poll_skips_known_trade(self, db: Database) -> None:
        """Polling twice with the same activity returns signals only once."""
        config = _make_config()
        api = _mock_api()

        # Discover + monitor setup
        discovery = WalletDiscovery(api, db, config.copy_trading)
        await discovery.discover()

        monitor = CopyTradeMonitor(api, db, config)
        await monitor.refresh_wallet_list()

        # First poll produces signal(s)
        first_signals = await monitor.poll_next()

        # Second poll with identical activity returns nothing new
        second_signals = await monitor.poll_next()
        assert len(second_signals) == 0, (
            "Known trade must be deduplicated on second poll"
        )
