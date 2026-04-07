"""Async SQLite database for Copywallet state persistence.

Provides WAL-mode SQLite via *aiosqlite* with schema auto-creation
and typed CRUD helpers for trades, positions, portfolio snapshots,
wallet profiles, known-trade deduplication, and market cache.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema DDL -- executed once on connect via CREATE TABLE IF NOT EXISTS
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS trades (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT,
    market_id       TEXT,
    token_id        TEXT,
    question        TEXT,
    category        TEXT,
    side            TEXT,
    price           REAL,
    size_usd        REAL,
    fee             REAL,
    signal_source   TEXT,
    signal_data     TEXT,
    order_type      TEXT,
    status          TEXT,
    leader_address  TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT,
    token_id        TEXT,
    side            TEXT,
    entry_price     REAL,
    size_shares     REAL,
    size_usd        REAL,
    current_price   REAL,
    unrealized_pnl  REAL,
    signal_source   TEXT,
    leader_address  TEXT,
    opened_at       TEXT,
    UNIQUE(market_id, side, signal_source)
);

CREATE TABLE IF NOT EXISTS portfolio (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT,
    total_value     REAL,
    cash_balance    REAL,
    positions_value REAL,
    daily_pnl       REAL,
    total_pnl       REAL
);

CREATE TABLE IF NOT EXISTS wallet_profiles (
    address         TEXT PRIMARY KEY,
    username        TEXT,
    category_stats  TEXT,
    total_pnl       REAL,
    total_volume    REAL,
    is_suspicious   INTEGER,
    tier            INTEGER DEFAULT 4,
    quality_score   REAL DEFAULT 0.0,
    last_scored     TEXT
);

CREATE TABLE IF NOT EXISTS known_trades (
    trade_id        TEXT PRIMARY KEY,
    wallet_address  TEXT,
    detected_at     TEXT
);

CREATE TABLE IF NOT EXISTS market_cache (
    market_id       TEXT PRIMARY KEY,
    condition_id    TEXT,
    token_id_yes    TEXT,
    token_id_no     TEXT,
    question        TEXT,
    category        TEXT,
    neg_risk        INTEGER,
    cached_at       TEXT
);
"""


class Database:
    """Async SQLite wrapper with WAL mode and Copywallet-specific CRUD.

    Args:
        path: filesystem path for the database file, or ``':memory:'``
              for an ephemeral in-memory database (useful in tests).
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the connection, enable WAL mode, and create schema.

        Safe to call multiple times -- uses ``CREATE TABLE IF NOT EXISTS``.
        Runs lightweight migrations for columns added after initial release.
        """
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row

        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()

        # --- Migrations for existing databases ---
        await self._migrate_wallet_tiers()

        logger.info("Database connected (%s), WAL enabled, schema ready", self._path)

    async def _migrate_wallet_tiers(self) -> None:
        """Add tier and quality_score columns if missing (v2 schema)."""
        cursor = await self._db.execute("PRAGMA table_info(wallet_profiles)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "tier" not in columns:
            await self._db.execute(
                "ALTER TABLE wallet_profiles ADD COLUMN tier INTEGER DEFAULT 4"
            )
            logger.info("Migrated wallet_profiles: added tier column")
        if "quality_score" not in columns:
            await self._db.execute(
                "ALTER TABLE wallet_profiles ADD COLUMN quality_score REAL DEFAULT 0.0"
            )
            logger.info("Migrated wallet_profiles: added quality_score column")
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection gracefully."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("Database connection closed")

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    async def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        """Execute *sql* with *params*, commit, and return the cursor."""
        cursor = await self._db.execute(sql, params)
        await self._db.commit()
        return cursor

    async def fetch_one(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        """Execute *sql* and return the first row as a dict, or ``None``."""
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        """Execute *sql* and return all rows as a list of dicts."""
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Trade operations
    # ------------------------------------------------------------------

    async def insert_trade(self, trade: Dict[str, Any]) -> None:
        """Insert a trade record, silently skipping duplicates.

        Uses ``INSERT OR IGNORE`` so re-inserting the same ``id`` is a no-op.
        """
        await self.execute(
            "INSERT OR IGNORE INTO trades "
            "(id, timestamp, market_id, token_id, question, category, "
            "side, price, size_usd, fee, signal_source, signal_data, "
            "order_type, status, leader_address) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade["id"],
                trade["timestamp"],
                trade["market_id"],
                trade["token_id"],
                trade["question"],
                trade["category"],
                trade["side"],
                trade["price"],
                trade["size_usd"],
                trade["fee"],
                trade["signal_source"],
                trade["signal_data"],
                trade["order_type"],
                trade["status"],
                trade["leader_address"],
            ),
        )

    async def get_recent_trades(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the *limit* most recent trades, newest first."""
        return await self.fetch_all(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------------
    # Position operations
    # ------------------------------------------------------------------

    async def upsert_position(self, pos: Dict[str, Any]) -> None:
        """Insert a position or accumulate into an existing one.

        On conflict (same ``market_id``, ``side``, ``signal_source``),
        ``size_shares`` and ``size_usd`` are added to the existing values
        while other fields are updated to the latest values.
        """
        await self.execute(
            "INSERT INTO positions "
            "(market_id, token_id, side, entry_price, size_shares, size_usd, "
            "current_price, unrealized_pnl, signal_source, leader_address, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(market_id, side, signal_source) DO UPDATE SET "
            "size_shares = size_shares + excluded.size_shares, "
            "size_usd = size_usd + excluded.size_usd, "
            "current_price = excluded.current_price, "
            "unrealized_pnl = excluded.unrealized_pnl",
            (
                pos["market_id"],
                pos["token_id"],
                pos["side"],
                pos["entry_price"],
                pos["size_shares"],
                pos["size_usd"],
                pos["current_price"],
                pos["unrealized_pnl"],
                pos["signal_source"],
                pos["leader_address"],
                pos["opened_at"],
            ),
        )

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return all current positions."""
        return await self.fetch_all("SELECT * FROM positions")

    async def get_position_count(self) -> int:
        """Return the number of open positions."""
        row = await self.fetch_one("SELECT COUNT(*) AS cnt FROM positions")
        return row["cnt"] if row else 0

    async def remove_position(self, market_id: str, side: str, signal_source: str) -> None:
        """Delete a position by its composite key."""
        await self.execute(
            "DELETE FROM positions "
            "WHERE market_id = ? AND side = ? AND signal_source = ?",
            (market_id, side, signal_source),
        )

    # ------------------------------------------------------------------
    # Portfolio snapshots
    # ------------------------------------------------------------------

    async def snapshot_portfolio(
        self,
        total_value: float,
        cash: float,
        positions_value: float,
        daily_pnl: float,
        total_pnl: float,
    ) -> None:
        """Record a point-in-time portfolio snapshot with an ISO timestamp."""
        await self.execute(
            "INSERT INTO portfolio "
            "(timestamp, total_value, cash_balance, positions_value, "
            "daily_pnl, total_pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.utcnow().isoformat(),
                total_value,
                cash,
                positions_value,
                daily_pnl,
                total_pnl,
            ),
        )

    # ------------------------------------------------------------------
    # Wallet profiles
    # ------------------------------------------------------------------

    async def upsert_wallet(self, wallet: Dict[str, Any]) -> None:
        """Insert or fully replace a wallet profile.

        ``category_stats`` is serialised to JSON before storage.
        """
        category_stats_raw = wallet["category_stats"]
        category_stats_json = (
            json.dumps(category_stats_raw)
            if isinstance(category_stats_raw, dict)
            else category_stats_raw
        )

        await self.execute(
            "INSERT OR REPLACE INTO wallet_profiles "
            "(address, username, category_stats, total_pnl, total_volume, "
            "is_suspicious, tier, quality_score, last_scored) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                wallet["address"],
                wallet["username"],
                category_stats_json,
                wallet["total_pnl"],
                wallet["total_volume"],
                wallet["is_suspicious"],
                wallet.get("tier", 4),
                wallet.get("quality_score", 0.0),
                wallet["last_scored"],
            ),
        )

    async def get_tracked_wallets(self, max_tier: int = 3) -> List[Dict[str, Any]]:
        """Return wallet profiles up to *max_tier* with parsed category_stats.

        Tier 1 = proven, 2 = promising, 3 = experimental, 4 = suspicious.
        Default returns tiers 1-3 (copyable).  Pass ``max_tier=4`` to
        include suspicious wallets (for data tracking only).

        The ``category_stats`` column is deserialised from JSON to a dict.
        """
        rows = await self.fetch_all(
            "SELECT * FROM wallet_profiles WHERE tier <= ? ORDER BY tier, quality_score DESC",
            (max_tier,),
        )
        for row in rows:
            raw = row.get("category_stats")
            if isinstance(raw, str):
                row["category_stats"] = json.loads(raw)
        return rows

    async def get_all_wallets(self) -> List[Dict[str, Any]]:
        """Return ALL wallet profiles regardless of tier, for data collection."""
        rows = await self.fetch_all(
            "SELECT * FROM wallet_profiles ORDER BY tier, quality_score DESC"
        )
        for row in rows:
            raw = row.get("category_stats")
            if isinstance(raw, str):
                row["category_stats"] = json.loads(raw)
        return rows

    # ------------------------------------------------------------------
    # Known trades (deduplication)
    # ------------------------------------------------------------------

    async def is_known_trade(self, trade_id: str) -> bool:
        """Return ``True`` if *trade_id* has already been recorded."""
        row = await self.fetch_one(
            "SELECT 1 FROM known_trades WHERE trade_id = ?",
            (trade_id,),
        )
        return row is not None

    async def mark_trade_known(self, trade_id: str, wallet: str) -> None:
        """Record *trade_id* so future ``is_known_trade`` calls return ``True``.

        Uses ``INSERT OR IGNORE`` for idempotency.
        """
        await self.execute(
            "INSERT OR IGNORE INTO known_trades "
            "(trade_id, wallet_address, detected_at) VALUES (?, ?, ?)",
            (trade_id, wallet, datetime.utcnow().isoformat()),
        )

    # ------------------------------------------------------------------
    # Market cache
    # ------------------------------------------------------------------

    async def cache_market(self, market: Dict[str, Any]) -> None:
        """Cache market metadata, replacing stale entries on conflict."""
        await self.execute(
            "INSERT OR REPLACE INTO market_cache "
            "(market_id, condition_id, token_id_yes, token_id_no, "
            "question, category, neg_risk, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                market["market_id"],
                market["condition_id"],
                market["token_id_yes"],
                market["token_id_no"],
                market["question"],
                market["category"],
                market["neg_risk"],
                market["cached_at"],
            ),
        )

    async def get_cached_market(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Return cached market metadata, or ``None`` if not cached."""
        return await self.fetch_one(
            "SELECT * FROM market_cache WHERE market_id = ?",
            (market_id,),
        )
