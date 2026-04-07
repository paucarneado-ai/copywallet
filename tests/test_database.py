"""Tests for the async SQLite database module.

Uses in-memory databases (``':memory:'``) for speed and isolation.
Every test gets a fresh ``Database`` instance via the ``db`` fixture.
"""

import json

import pytest
import pytest_asyncio

from src.state.database import Database

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """Yield a connected in-memory database, then close it."""
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Connection and schema
# ---------------------------------------------------------------------------


class TestConnection:
    """Verify connect, WAL mode, and schema creation."""

    @pytest.mark.asyncio
    async def test_connect_creates_tables(self, db: Database) -> None:
        """All six application tables must exist after connect."""
        rows = await db.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        table_names = sorted(r["name"] for r in rows)
        expected = sorted([
            "known_trades",
            "market_cache",
            "portfolio",
            "positions",
            "trades",
            "wallet_profiles",
        ])
        assert table_names == expected

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self) -> None:
        """WAL journal mode must be active after connect on a file-backed DB."""
        import tempfile
        import os

        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            database = Database(tmp_path)
            await database.connect()
            row = await database.fetch_one("PRAGMA journal_mode")
            assert row is not None
            assert row["journal_mode"] == "wal"
            await database.close()
        finally:
            for suffix in ("", "-shm", "-wal"):
                path = tmp_path + suffix
                if os.path.exists(path):
                    os.remove(path)

    @pytest.mark.asyncio
    async def test_connect_is_idempotent(self) -> None:
        """Calling connect twice must not raise or corrupt state."""
        database = Database(":memory:")
        await database.connect()
        await database.connect()
        rows = await database.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        assert len(rows) == 6
        await database.close()


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


class TestLowLevelHelpers:
    """Verify execute, fetch_one, and fetch_all."""

    @pytest.mark.asyncio
    async def test_execute_returns_cursor(self, db: Database) -> None:
        """execute() must return a cursor-like object with lastrowid."""
        cursor = await db.execute(
            "INSERT INTO portfolio (timestamp, total_value, cash_balance, "
            "positions_value, daily_pnl, total_pnl) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("2025-01-01T00:00:00", 100.0, 100.0, 0.0, 0.0, 0.0),
        )
        assert cursor.lastrowid is not None

    @pytest.mark.asyncio
    async def test_fetch_one_returns_dict(self, db: Database) -> None:
        """fetch_one() must return a dict-like row."""
        await db.execute(
            "INSERT INTO portfolio (timestamp, total_value, cash_balance, "
            "positions_value, daily_pnl, total_pnl) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("2025-01-01T00:00:00", 100.0, 100.0, 0.0, 0.0, 0.0),
        )
        row = await db.fetch_one("SELECT * FROM portfolio LIMIT 1")
        assert row is not None
        assert row["total_value"] == 100.0

    @pytest.mark.asyncio
    async def test_fetch_one_returns_none_for_empty(self, db: Database) -> None:
        """fetch_one() must return None when no rows match."""
        row = await db.fetch_one("SELECT * FROM portfolio LIMIT 1")
        assert row is None

    @pytest.mark.asyncio
    async def test_fetch_all_returns_list_of_dicts(self, db: Database) -> None:
        """fetch_all() must return a list of dict-like rows."""
        for i in range(3):
            await db.execute(
                "INSERT INTO portfolio (timestamp, total_value, cash_balance, "
                "positions_value, daily_pnl, total_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"2025-01-0{i + 1}T00:00:00", 100.0 + i, 100.0, 0.0, 0.0, 0.0),
            )
        rows = await db.fetch_all("SELECT * FROM portfolio")
        assert len(rows) == 3
        assert all(isinstance(r["total_value"], float) for r in rows)

    @pytest.mark.asyncio
    async def test_fetch_all_returns_empty_list(self, db: Database) -> None:
        """fetch_all() must return [] when no rows match."""
        rows = await db.fetch_all("SELECT * FROM trades")
        assert rows == []


# ---------------------------------------------------------------------------
# Trade operations
# ---------------------------------------------------------------------------


class TestTradeOperations:
    """Verify insert_trade and get_recent_trades."""

    @staticmethod
    def _make_trade(trade_id: str = "t1", timestamp: str = "2025-01-01T12:00:00") -> dict:
        """Build a minimal trade dict."""
        return {
            "id": trade_id,
            "timestamp": timestamp,
            "market_id": "mkt1",
            "token_id": "tok1",
            "question": "Will X happen?",
            "category": "crypto",
            "side": "BUY",
            "price": 0.55,
            "size_usd": 25.0,
            "fee": 0.45,
            "signal_source": "copy",
            "signal_data": "{}",
            "order_type": "GTC",
            "status": "filled",
            "leader_address": "0xabc",
        }

    @pytest.mark.asyncio
    async def test_insert_trade(self, db: Database) -> None:
        """A trade can be inserted and retrieved."""
        await db.insert_trade(self._make_trade())
        rows = await db.fetch_all("SELECT * FROM trades")
        assert len(rows) == 1
        assert rows[0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_insert_trade_ignores_duplicate(self, db: Database) -> None:
        """INSERT OR IGNORE must skip duplicates silently."""
        trade = self._make_trade()
        await db.insert_trade(trade)
        await db.insert_trade(trade)
        rows = await db.fetch_all("SELECT * FROM trades")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_get_recent_trades_ordered_desc(self, db: Database) -> None:
        """Trades must be returned newest-first."""
        for i in range(5):
            t = self._make_trade(
                trade_id=f"t{i}",
                timestamp=f"2025-01-0{i + 1}T00:00:00",
            )
            await db.insert_trade(t)

        recent = await db.get_recent_trades(limit=3)
        assert len(recent) == 3
        assert recent[0]["id"] == "t4"
        assert recent[1]["id"] == "t3"
        assert recent[2]["id"] == "t2"

    @pytest.mark.asyncio
    async def test_get_recent_trades_default_limit(self, db: Database) -> None:
        """Default limit is 10."""
        for i in range(15):
            t = self._make_trade(
                trade_id=f"t{i:02d}",
                timestamp=f"2025-01-{i + 1:02d}T00:00:00",
            )
            await db.insert_trade(t)

        recent = await db.get_recent_trades()
        assert len(recent) == 10


# ---------------------------------------------------------------------------
# Position operations
# ---------------------------------------------------------------------------


class TestPositionOperations:
    """Verify upsert_position, get_open_positions, get_position_count, remove_position."""

    @staticmethod
    def _make_position(
        market_id: str = "mkt1",
        side: str = "BUY",
        signal_source: str = "copy",
    ) -> dict:
        """Build a minimal position dict."""
        return {
            "market_id": market_id,
            "token_id": "tok1",
            "side": side,
            "entry_price": 0.55,
            "size_shares": 10.0,
            "size_usd": 5.50,
            "current_price": 0.55,
            "unrealized_pnl": 0.0,
            "signal_source": signal_source,
            "leader_address": "0xabc",
            "opened_at": "2025-01-01T12:00:00",
        }

    @pytest.mark.asyncio
    async def test_upsert_position_insert(self, db: Database) -> None:
        """First upsert creates a new row."""
        await db.upsert_position(self._make_position())
        positions = await db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["size_shares"] == 10.0

    @pytest.mark.asyncio
    async def test_upsert_position_accumulates(self, db: Database) -> None:
        """Second upsert with same key must accumulate size_shares and size_usd."""
        pos = self._make_position()
        await db.upsert_position(pos)
        await db.upsert_position(pos)

        positions = await db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["size_shares"] == pytest.approx(20.0)
        assert positions[0]["size_usd"] == pytest.approx(11.0)

    @pytest.mark.asyncio
    async def test_upsert_position_different_keys(self, db: Database) -> None:
        """Different (market_id, side, signal_source) must create separate rows."""
        await db.upsert_position(self._make_position(market_id="mkt1"))
        await db.upsert_position(self._make_position(market_id="mkt2"))
        assert await db.get_position_count() == 2

    @pytest.mark.asyncio
    async def test_get_position_count(self, db: Database) -> None:
        """get_position_count() returns correct integer."""
        assert await db.get_position_count() == 0
        await db.upsert_position(self._make_position())
        assert await db.get_position_count() == 1

    @pytest.mark.asyncio
    async def test_remove_position(self, db: Database) -> None:
        """remove_position() deletes by composite key."""
        await db.upsert_position(self._make_position())
        assert await db.get_position_count() == 1

        await db.remove_position("mkt1", "BUY", "copy")
        assert await db.get_position_count() == 0

    @pytest.mark.asyncio
    async def test_remove_position_no_match(self, db: Database) -> None:
        """Removing a non-existent position must not raise."""
        await db.remove_position("nonexistent", "BUY", "copy")
        assert await db.get_position_count() == 0


# ---------------------------------------------------------------------------
# Portfolio snapshots
# ---------------------------------------------------------------------------


class TestPortfolioSnapshot:
    """Verify snapshot_portfolio."""

    @pytest.mark.asyncio
    async def test_snapshot_portfolio(self, db: Database) -> None:
        """snapshot_portfolio() must insert a timestamped row."""
        await db.snapshot_portfolio(
            total_value=1000.0,
            cash=800.0,
            positions_value=200.0,
            daily_pnl=10.0,
            total_pnl=50.0,
        )
        rows = await db.fetch_all("SELECT * FROM portfolio")
        assert len(rows) == 1
        assert rows[0]["total_value"] == 1000.0
        assert rows[0]["cash_balance"] == 800.0
        assert rows[0]["timestamp"] is not None


# ---------------------------------------------------------------------------
# Wallet profiles
# ---------------------------------------------------------------------------


class TestWalletProfiles:
    """Verify upsert_wallet and get_tracked_wallets."""

    @staticmethod
    def _make_wallet(
        address: str = "0xwallet1",
        is_suspicious: int = 0,
        tier: int = 2,
        quality_score: float = 50.0,
    ) -> dict:
        """Build a minimal wallet dict."""
        return {
            "address": address,
            "username": "trader1",
            "category_stats": {"crypto": {"win_rate": 0.6}},
            "total_pnl": 500.0,
            "total_volume": 10000.0,
            "is_suspicious": is_suspicious,
            "tier": tier,
            "quality_score": quality_score,
            "last_scored": "2025-01-01T00:00:00",
        }

    @pytest.mark.asyncio
    async def test_upsert_wallet(self, db: Database) -> None:
        """upsert_wallet() inserts a new wallet profile."""
        await db.upsert_wallet(self._make_wallet())
        rows = await db.fetch_all("SELECT * FROM wallet_profiles")
        assert len(rows) == 1
        assert rows[0]["address"] == "0xwallet1"

    @pytest.mark.asyncio
    async def test_upsert_wallet_replaces(self, db: Database) -> None:
        """upsert_wallet() replaces existing wallet on conflict."""
        await db.upsert_wallet(self._make_wallet())
        updated = self._make_wallet()
        updated["total_pnl"] = 999.0
        await db.upsert_wallet(updated)

        rows = await db.fetch_all("SELECT * FROM wallet_profiles")
        assert len(rows) == 1
        assert rows[0]["total_pnl"] == 999.0

    @pytest.mark.asyncio
    async def test_upsert_wallet_stores_category_stats_as_json(self, db: Database) -> None:
        """category_stats must be stored as a JSON string."""
        await db.upsert_wallet(self._make_wallet())
        row = await db.fetch_one(
            "SELECT category_stats FROM wallet_profiles WHERE address = ?",
            ("0xwallet1",),
        )
        assert row is not None
        parsed = json.loads(row["category_stats"])
        assert parsed["crypto"]["win_rate"] == 0.6

    @pytest.mark.asyncio
    async def test_get_tracked_wallets_excludes_tier4(self, db: Database) -> None:
        """get_tracked_wallets() must filter out tier-4 (suspicious) wallets."""
        await db.upsert_wallet(self._make_wallet("0xgood", tier=1, quality_score=80.0))
        await db.upsert_wallet(self._make_wallet("0xbad", is_suspicious=1, tier=4, quality_score=0.0))

        tracked = await db.get_tracked_wallets()
        assert len(tracked) == 1
        assert tracked[0]["address"] == "0xgood"

    @pytest.mark.asyncio
    async def test_get_all_wallets_includes_tier4(self, db: Database) -> None:
        """get_all_wallets() returns all tiers including suspicious."""
        await db.upsert_wallet(self._make_wallet("0xgood", tier=1, quality_score=80.0))
        await db.upsert_wallet(self._make_wallet("0xbad", is_suspicious=1, tier=4, quality_score=0.0))

        all_wallets = await db.get_all_wallets()
        assert len(all_wallets) == 2

    @pytest.mark.asyncio
    async def test_get_tracked_wallets_parses_category_stats(self, db: Database) -> None:
        """get_tracked_wallets() must parse category_stats from JSON string to dict."""
        await db.upsert_wallet(self._make_wallet())
        tracked = await db.get_tracked_wallets()
        assert isinstance(tracked[0]["category_stats"], dict)
        assert tracked[0]["category_stats"]["crypto"]["win_rate"] == 0.6


# ---------------------------------------------------------------------------
# Known trades (dedup)
# ---------------------------------------------------------------------------


class TestKnownTrades:
    """Verify is_known_trade and mark_trade_known."""

    @pytest.mark.asyncio
    async def test_unknown_trade(self, db: Database) -> None:
        """An unmarked trade_id must return False."""
        assert await db.is_known_trade("unknown_id") is False

    @pytest.mark.asyncio
    async def test_mark_and_check_known(self, db: Database) -> None:
        """After marking, is_known_trade must return True."""
        await db.mark_trade_known("trade_abc", "0xwallet")
        assert await db.is_known_trade("trade_abc") is True

    @pytest.mark.asyncio
    async def test_mark_trade_known_idempotent(self, db: Database) -> None:
        """Marking the same trade_id twice must not raise."""
        await db.mark_trade_known("trade_abc", "0xwallet")
        await db.mark_trade_known("trade_abc", "0xwallet")
        assert await db.is_known_trade("trade_abc") is True


# ---------------------------------------------------------------------------
# Market cache
# ---------------------------------------------------------------------------


class TestMarketCache:
    """Verify cache_market and get_cached_market."""

    @staticmethod
    def _make_market(market_id: str = "mkt1") -> dict:
        """Build a minimal market dict."""
        return {
            "market_id": market_id,
            "condition_id": "cond1",
            "token_id_yes": "tok_yes",
            "token_id_no": "tok_no",
            "question": "Will X happen?",
            "category": "crypto",
            "neg_risk": 0,
            "cached_at": "2025-01-01T00:00:00",
        }

    @pytest.mark.asyncio
    async def test_cache_market(self, db: Database) -> None:
        """cache_market() must insert a market row."""
        await db.cache_market(self._make_market())
        row = await db.get_cached_market("mkt1")
        assert row is not None
        assert row["question"] == "Will X happen?"

    @pytest.mark.asyncio
    async def test_cache_market_replaces(self, db: Database) -> None:
        """cache_market() must replace on conflict."""
        await db.cache_market(self._make_market())
        updated = self._make_market()
        updated["question"] = "Updated?"
        await db.cache_market(updated)

        row = await db.get_cached_market("mkt1")
        assert row is not None
        assert row["question"] == "Updated?"

    @pytest.mark.asyncio
    async def test_get_cached_market_miss(self, db: Database) -> None:
        """get_cached_market() must return None for unknown market_id."""
        row = await db.get_cached_market("nonexistent")
        assert row is None
