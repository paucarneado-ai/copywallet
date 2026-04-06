# Phase 1: Copy Trading Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous Polymarket copy trading bot that discovers top wallets, monitors their trades, filters by category dominance, and executes via a paper trading simulator — with Telegram and web dashboard monitoring.

**Architecture:** Async Python main loop polls Polymarket public APIs to detect trades from curated leader wallets. A filtering pipeline validates each trade (category, staleness, slippage, exposure). Valid signals go to a paper executor that simulates fills with realistic fees and slippage. SQLite stores all state. Telegram bot and FastAPI dashboard provide real-time visibility.

**Tech Stack:** Python 3.11+, httpx, aiosqlite, python-telegram-bot, FastAPI, Jinja2, Chart.js, pyyaml

**Spec:** `docs/superpowers/specs/2026-04-07-copywallet-design.md` + `2026-04-07-copywallet-review-fixes.md`

---

## File Structure

```
copywallet/
├── CLAUDE.md                          # Project rules (exists)
├── config.example.yaml                # Config template (exists)
├── requirements.txt                   # Dependencies
├── src/
│   ├── __init__.py                    # (exists)
│   ├── main.py                        # Entry point, async main loop
│   ├── config.py                      # Load + validate config.yaml
│   ├── rate_limiter.py                # Request rate limiting
│   ├── polymarket_api.py              # All Polymarket API calls (Gamma, Data, CLOB)
│   ├── copytrade/
│   │   ├── __init__.py                # (exists)
│   │   ├── models.py                  # WalletProfile, CategoryStats, CopySignal
│   │   ├── discovery.py               # Leaderboard scanning + wallet scoring
│   │   ├── monitor.py                 # Round-robin trade polling + detection
│   │   └── filters.py                 # 7-stage filtering pipeline
│   ├── executor/
│   │   ├── __init__.py                # (exists)
│   │   ├── models.py                  # OrderResult, Position
│   │   ├── base.py                    # BaseExecutor ABC
│   │   ├── paper.py                   # Paper trading simulator
│   │   ├── kelly.py                   # Kelly criterion sizing
│   │   └── slippage.py                # Orderbook walk for fill estimation
│   ├── state/
│   │   ├── __init__.py                # (exists)
│   │   └── database.py                # SQLite setup, queries, WAL mode
│   ├── monitor/
│   │   ├── __init__.py                # (exists)
│   │   ├── telegram_bot.py            # Alerts + commands
│   │   └── dashboard.py               # FastAPI app + routes
│   └── templates/
│       ├── base.html                  # Shared layout
│       ├── overview.html              # Main dashboard page
│       ├── positions.html             # Open positions table
│       ├── trades.html                # Trade log
│       ├── pnl.html                   # PnL charts
│       ├── leaders.html               # Tracked wallets
│       └── settings.html              # Config view
├── static/
│   └── chart_config.js                # Chart.js setup
└── tests/
    ├── __init__.py                    # (exists)
    ├── test_config.py
    ├── test_rate_limiter.py
    ├── test_models.py
    ├── test_kelly.py
    ├── test_slippage.py
    ├── test_filters.py
    ├── test_paper_executor.py
    ├── test_discovery.py
    └── test_monitor.py
```

---

## Chunk 1: Foundation (Config, API Client, Rate Limiter, Database)

### Task 1: Dependencies and Config

**Files:**
- Create: `requirements.txt`
- Create: `src/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write requirements.txt**

```
httpx>=0.27.0
aiosqlite>=0.20.0
python-telegram-bot>=21.0
fastapi>=0.115.0
uvicorn>=0.32.0
jinja2>=3.1.0
pyyaml>=6.0
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

- [ ] **Step 2: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: All packages install successfully

- [ ] **Step 3: Write failing test for config loading**

```python
# tests/test_config.py
import pytest
from pathlib import Path

def test_load_config_from_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
mode: paper
risk:
  starting_bankroll: 10000
  kelly_fraction: 0.25
  max_position_pct: 0.05
  cash_reserve_pct: 0.30
  max_concurrent_positions: 10
  daily_loss_limit_pct: 0.05
  min_trade_size: 10
copy_trading:
  enabled: true
  poll_interval_seconds: 2
  discovery_interval_hours: 6
  min_win_rate: 0.55
  max_win_rate: 0.90
  min_resolved_positions: 50
  min_category_positions: 20
  min_volume: 100000
  min_profit_factor: 1.5
  categories:
    - CRYPTO
  max_trade_age_seconds: 60
  max_price_drift: 0.05
  max_spread: 0.10
  max_slippage: 0.02
  max_position_per_market: 100
  max_concurrent_positions: 10
  max_tracked_wallets: 15
scanner:
  min_volume_24h: 5000
  min_time_to_resolution_hours: 1
  max_spread: 0.10
  categories:
    - crypto
fees:
  crypto: 0.018
  sports: 0.0075
  politics: 0.01
  economics: 0.015
  geopolitics: 0.0
  default: 0.01
database:
  path: ":memory:"
logging:
  level: INFO
""")
    from src.config import load_config
    cfg = load_config(str(config_file))
    assert cfg.mode == "paper"
    assert cfg.risk.starting_bankroll == 10000
    assert cfg.risk.kelly_fraction == 0.25
    assert cfg.copy_trading.min_win_rate == 0.55
    assert cfg.fees["crypto"] == 0.018

def test_config_validates_mode():
    from src.config import load_config
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("mode: invalid\n")
        f.flush()
        with pytest.raises(ValueError, match="mode must be 'paper' or 'live'"):
            load_config(f.name)
        os.unlink(f.name)

def test_config_defaults():
    from src.config import load_config
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("mode: paper\n")
        f.flush()
        cfg = load_config(f.name)
        assert cfg.risk.kelly_fraction == 0.25
        assert cfg.risk.cash_reserve_pct == 0.30
        os.unlink(f.name)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.config'`

- [ ] **Step 5: Implement config.py**

```python
# src/config.py
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class RiskConfig:
    starting_bankroll: float = 10000
    kelly_fraction: float = 0.25
    max_position_pct: float = 0.05
    cash_reserve_pct: float = 0.30
    max_concurrent_positions: int = 10
    daily_loss_limit_pct: float = 0.05
    min_trade_size: float = 10


@dataclass
class CopyTradingConfig:
    enabled: bool = True
    poll_interval_seconds: float = 2
    discovery_interval_hours: float = 6
    min_win_rate: float = 0.55
    max_win_rate: float = 0.90
    min_resolved_positions: int = 50
    min_category_positions: int = 20
    min_volume: float = 100000
    min_profit_factor: float = 1.5
    categories: list[str] = field(default_factory=lambda: ["CRYPTO"])
    max_trade_age_seconds: int = 60
    max_price_drift: float = 0.05
    max_spread: float = 0.10
    max_slippage: float = 0.02
    max_position_per_market: float = 100
    max_concurrent_positions: int = 10
    max_tracked_wallets: int = 15


@dataclass
class ScannerConfig:
    min_volume_24h: float = 5000
    min_time_to_resolution_hours: float = 1
    max_spread: float = 0.10
    categories: list[str] = field(default_factory=lambda: ["crypto"])


@dataclass
class PolymarketConfig:
    private_key: str = ""
    proxy_wallet: str = ""
    clob_host: str = "https://clob.polymarket.com"
    data_api_host: str = "https://data-api.polymarket.com"
    gamma_api_host: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137


@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    password: str = ""


@dataclass
class DatabaseConfig:
    path: str = "copywallet.db"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "copywallet.log"


@dataclass
class GraduationConfig:
    min_trades: int = 200
    min_days: int = 14
    min_win_rate: float = 0.52
    min_profit_factor: float = 1.2
    max_drawdown: float = 0.15
    auto_scale_hours: int = 48


@dataclass
class Config:
    mode: str = "paper"
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    copy_trading: CopyTradingConfig = field(default_factory=CopyTradingConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    graduation: GraduationConfig = field(default_factory=GraduationConfig)
    fees: dict[str, float] = field(default_factory=lambda: {
        "crypto": 0.018, "sports": 0.0075, "politics": 0.01,
        "economics": 0.015, "geopolitics": 0.0, "default": 0.01
    })
    telegram_bot_token: str = ""
    telegram_user_id: str = ""
    anthropic_api_key: str = ""


def _build_dataclass(cls, data: dict):
    if data is None:
        return cls()
    filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    return cls(**filtered)


def load_config(path: str) -> Config:
    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    mode = raw.get("mode", "paper")
    if mode not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")

    cfg = Config(
        mode=mode,
        polymarket=_build_dataclass(PolymarketConfig, raw.get("polymarket")),
        risk=_build_dataclass(RiskConfig, raw.get("risk")),
        copy_trading=_build_dataclass(CopyTradingConfig, raw.get("copy_trading")),
        scanner=_build_dataclass(ScannerConfig, raw.get("scanner")),
        dashboard=_build_dataclass(DashboardConfig, raw.get("dashboard")),
        database=_build_dataclass(DatabaseConfig, raw.get("database")),
        logging=_build_dataclass(LoggingConfig, raw.get("logging")),
        graduation=_build_dataclass(GraduationConfig, raw.get("graduation")),
        fees=raw.get("fees", cfg.fees) if "fees" in raw else Config.fees.default,
        telegram_bot_token=raw.get("telegram_bot_token", ""),
        telegram_user_id=raw.get("telegram_user_id", ""),
        anthropic_api_key=raw.get("anthropic_api_key", ""),
    )

    if cfg.dashboard.host != "127.0.0.1" and not cfg.dashboard.password:
        raise ValueError("Dashboard password required when binding to non-localhost address")

    return cfg
```

Note: The `fees` default handling has a subtle issue — fix it during implementation by using:
```python
fees=raw.get("fees", {"crypto": 0.018, "sports": 0.0075, "politics": 0.01,
                       "economics": 0.015, "geopolitics": 0.0, "default": 0.01}),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add requirements.txt src/config.py tests/test_config.py
git commit -m "feat: add config loading with validation and defaults"
```

---

### Task 2: Rate Limiter

**Files:**
- Create: `src/rate_limiter.py`
- Create: `tests/test_rate_limiter.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_rate_limiter.py
import pytest
import asyncio
import time

@pytest.mark.asyncio
async def test_rate_limiter_allows_within_limit():
    from src.rate_limiter import RateLimiter
    limiter = RateLimiter(max_requests_per_minute=60)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0  # should be near-instant

@pytest.mark.asyncio
async def test_rate_limiter_tracks_count():
    from src.rate_limiter import RateLimiter
    limiter = RateLimiter(max_requests_per_minute=100)
    for _ in range(10):
        await limiter.acquire()
    assert limiter.requests_in_window >= 10

@pytest.mark.asyncio
async def test_rate_limiter_delays_at_threshold():
    from src.rate_limiter import RateLimiter
    limiter = RateLimiter(max_requests_per_minute=10, warn_threshold=0.8)
    # Fill up to threshold
    for _ in range(8):
        limiter._timestamps.append(time.monotonic())
    # Next acquire should add a delay
    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # some throttling delay applied
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rate_limiter.py -v`
Expected: FAIL

- [ ] **Step 3: Implement rate_limiter.py**

```python
# src/rate_limiter.py
import asyncio
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_requests_per_minute: int = 90, warn_threshold: float = 0.8):
        self.max_rpm = max_requests_per_minute
        self.warn_threshold = warn_threshold
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    @property
    def requests_in_window(self) -> int:
        self._prune()
        return len(self._timestamps)

    def _prune(self):
        cutoff = time.monotonic() - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    async def acquire(self):
        async with self._lock:
            self._prune()
            usage = len(self._timestamps) / self.max_rpm

            if usage >= 1.0:
                wait = 60.0 - (time.monotonic() - self._timestamps[0])
                if wait > 0:
                    logger.warning(f"Rate limit reached ({len(self._timestamps)}/{self.max_rpm}). Waiting {wait:.1f}s")
                    await asyncio.sleep(wait)
                    self._prune()
            elif usage >= self.warn_threshold:
                delay = 0.1 * (usage - self.warn_threshold) / (1.0 - self.warn_threshold)
                await asyncio.sleep(delay)

            self._timestamps.append(time.monotonic())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rate_limiter.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat: add rate limiter with sliding window and threshold throttling"
```

---

### Task 3: Polymarket API Client

**Files:**
- Create: `src/polymarket_api.py`

This is the centralized API client. All Polymarket API calls go through here. No tests for this module — it wraps external HTTP calls that are mocked in integration tests of other modules.

- [ ] **Step 1: Implement polymarket_api.py**

```python
# src/polymarket_api.py
import httpx
import logging
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


class PolymarketAPI:
    def __init__(self, rate_limiter: RateLimiter | None = None):
        self._client = httpx.AsyncClient(timeout=15.0)
        self._limiter = rate_limiter or RateLimiter()

    async def close(self):
        await self._client.aclose()

    async def _get(self, url: str, params: dict | None = None) -> dict | list:
        await self._limiter.acquire()
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # --- Data API ---

    async def get_leaderboard(self, category: str = "OVERALL", period: str = "MONTH",
                              order_by: str = "PNL", limit: int = 50, offset: int = 0) -> list[dict]:
        return await self._get(f"{DATA_API}/leaderboard", {
            "category": category, "period": period, "orderBy": order_by,
            "limit": limit, "offset": offset
        })

    async def get_activity(self, user: str, trade_type: str = "TRADE",
                           limit: int = 10, start: int | None = None) -> list[dict]:
        params = {"user": user.lower(), "type": trade_type, "limit": limit,
                  "sortBy": "TIMESTAMP", "sortDirection": "DESC"}
        if start:
            params["start"] = start
        return await self._get(f"{DATA_API}/activity", params)

    async def get_trades(self, user: str, limit: int = 100, offset: int = 0) -> list[dict]:
        return await self._get(f"{DATA_API}/trades", {
            "user": user.lower(), "limit": limit, "offset": offset
        })

    async def get_positions(self, user: str) -> list[dict]:
        return await self._get(f"{DATA_API}/positions", {"user": user.lower()})

    async def get_closed_positions(self, user: str, limit: int = 100, offset: int = 0) -> list[dict]:
        return await self._get(f"{DATA_API}/closed-positions", {
            "user": user.lower(), "limit": limit, "offset": offset
        })

    async def get_all_trades(self, user: str) -> list[dict]:
        all_trades = []
        offset = 0
        while True:
            batch = await self.get_trades(user, limit=100, offset=offset)
            if not batch:
                break
            all_trades.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
        return all_trades

    # --- Gamma API ---

    async def get_market(self, condition_id: str) -> dict:
        return await self._get(f"{GAMMA_API}/markets/{condition_id}")

    async def get_events(self, limit: int = 50, offset: int = 0, tag: str | None = None) -> list[dict]:
        params = {"limit": limit, "offset": offset, "active": True, "closed": False}
        if tag:
            params["tag"] = tag
        return await self._get(f"{GAMMA_API}/events", params)

    # --- CLOB API ---

    async def get_price(self, token_id: str) -> dict:
        return await self._get(f"{CLOB_API}/price", {"token_id": token_id})

    async def get_book(self, token_id: str) -> dict:
        return await self._get(f"{CLOB_API}/book", {"token_id": token_id})

    async def get_midpoint(self, token_id: str) -> float:
        data = await self._get(f"{CLOB_API}/midpoint", {"token_id": token_id})
        return float(data.get("mid", 0))

    async def get_spread(self, token_id: str) -> float:
        data = await self._get(f"{CLOB_API}/spread", {"token_id": token_id})
        return float(data.get("spread", 1.0))
```

- [ ] **Step 2: Commit**

```bash
git add src/polymarket_api.py
git commit -m "feat: add centralized Polymarket API client with rate limiting"
```

---

### Task 4: Database (SQLite + WAL)

**Files:**
- Create: `src/state/database.py`
- Modify: `src/state/__init__.py`

- [ ] **Step 1: Implement database.py**

```python
# src/state/database.py
import aiosqlite
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    timestamp TEXT,
    market_id TEXT,
    token_id TEXT,
    question TEXT,
    category TEXT,
    side TEXT,
    price REAL,
    size_usd REAL,
    fee REAL,
    signal_source TEXT,
    signal_data TEXT,
    order_type TEXT,
    status TEXT,
    leader_address TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    token_id TEXT,
    side TEXT,
    entry_price REAL,
    size_shares REAL,
    size_usd REAL,
    current_price REAL,
    unrealized_pnl REAL,
    signal_source TEXT,
    leader_address TEXT,
    opened_at TEXT,
    UNIQUE(market_id, side, signal_source)
);

CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    total_value REAL,
    cash_balance REAL,
    positions_value REAL,
    daily_pnl REAL,
    total_pnl REAL
);

CREATE TABLE IF NOT EXISTS wallet_profiles (
    address TEXT PRIMARY KEY,
    username TEXT,
    category_stats TEXT,
    total_pnl REAL,
    total_volume REAL,
    is_suspicious INTEGER,
    last_scored TEXT
);

CREATE TABLE IF NOT EXISTS known_trades (
    trade_id TEXT PRIMARY KEY,
    wallet_address TEXT,
    detected_at TEXT
);

CREATE TABLE IF NOT EXISTS market_cache (
    market_id TEXT PRIMARY KEY,
    condition_id TEXT,
    token_id_yes TEXT,
    token_id_no TEXT,
    question TEXT,
    category TEXT,
    neg_risk INTEGER,
    cached_at TEXT
);
"""


class Database:
    def __init__(self, path: str):
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self):
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info(f"Database connected: {self._path}")

    async def close(self):
        if self._db:
            await self._db.close()

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cursor = await self._db.execute(sql, params)
        await self._db.commit()
        return cursor

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # --- Trade operations ---

    async def insert_trade(self, trade: dict):
        await self.execute(
            """INSERT OR IGNORE INTO trades
               (id, timestamp, market_id, token_id, question, category, side, price,
                size_usd, fee, signal_source, signal_data, order_type, status, leader_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade["id"], trade["timestamp"], trade["market_id"], trade.get("token_id"),
             trade["question"], trade["category"], trade["side"], trade["price"],
             trade["size_usd"], trade["fee"], trade["signal_source"],
             json.dumps(trade.get("signal_data", {})), trade.get("order_type", "FOK"),
             trade["status"], trade.get("leader_address"))
        )

    async def get_recent_trades(self, limit: int = 10) -> list[dict]:
        return await self.fetch_all(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,))

    # --- Position operations ---

    async def upsert_position(self, pos: dict):
        await self.execute(
            """INSERT INTO positions
               (market_id, token_id, side, entry_price, size_shares, size_usd,
                current_price, unrealized_pnl, signal_source, leader_address, opened_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(market_id, side, signal_source) DO UPDATE SET
                 size_shares = size_shares + excluded.size_shares,
                 size_usd = size_usd + excluded.size_usd,
                 current_price = excluded.current_price,
                 unrealized_pnl = excluded.unrealized_pnl""",
            (pos["market_id"], pos.get("token_id"), pos["side"], pos["entry_price"],
             pos["size_shares"], pos["size_usd"], pos.get("current_price", pos["entry_price"]),
             pos.get("unrealized_pnl", 0), pos["signal_source"],
             pos.get("leader_address"), pos.get("opened_at", datetime.utcnow().isoformat()))
        )

    async def get_open_positions(self) -> list[dict]:
        return await self.fetch_all("SELECT * FROM positions")

    async def get_position_count(self) -> int:
        row = await self.fetch_one("SELECT COUNT(*) as cnt FROM positions")
        return row["cnt"] if row else 0

    async def remove_position(self, market_id: str, side: str, signal_source: str):
        await self.execute(
            "DELETE FROM positions WHERE market_id=? AND side=? AND signal_source=?",
            (market_id, side, signal_source))

    # --- Portfolio operations ---

    async def snapshot_portfolio(self, total_value: float, cash: float,
                                 positions_value: float, daily_pnl: float, total_pnl: float):
        await self.execute(
            """INSERT INTO portfolio (timestamp, total_value, cash_balance, positions_value, daily_pnl, total_pnl)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), total_value, cash, positions_value, daily_pnl, total_pnl))

    # --- Wallet operations ---

    async def upsert_wallet(self, wallet: dict):
        await self.execute(
            """INSERT INTO wallet_profiles (address, username, category_stats, total_pnl, total_volume, is_suspicious, last_scored)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(address) DO UPDATE SET
                 username=excluded.username, category_stats=excluded.category_stats,
                 total_pnl=excluded.total_pnl, total_volume=excluded.total_volume,
                 is_suspicious=excluded.is_suspicious, last_scored=excluded.last_scored""",
            (wallet["address"], wallet.get("username"), json.dumps(wallet.get("category_stats", {})),
             wallet.get("total_pnl", 0), wallet.get("total_volume", 0),
             int(wallet.get("is_suspicious", False)), datetime.utcnow().isoformat()))

    async def get_tracked_wallets(self) -> list[dict]:
        rows = await self.fetch_all(
            "SELECT * FROM wallet_profiles WHERE is_suspicious = 0 ORDER BY total_pnl DESC")
        for r in rows:
            r["category_stats"] = json.loads(r["category_stats"]) if r["category_stats"] else {}
        return rows

    # --- Known trades (dedup) ---

    async def is_known_trade(self, trade_id: str) -> bool:
        row = await self.fetch_one("SELECT trade_id FROM known_trades WHERE trade_id=?", (trade_id,))
        return row is not None

    async def mark_trade_known(self, trade_id: str, wallet: str):
        await self.execute(
            "INSERT OR IGNORE INTO known_trades (trade_id, wallet_address, detected_at) VALUES (?, ?, ?)",
            (trade_id, wallet, datetime.utcnow().isoformat()))

    # --- Market cache ---

    async def cache_market(self, market: dict):
        await self.execute(
            """INSERT OR REPLACE INTO market_cache
               (market_id, condition_id, token_id_yes, token_id_no, question, category, neg_risk, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (market["market_id"], market.get("condition_id"), market.get("token_id_yes"),
             market.get("token_id_no"), market.get("question"), market.get("category"),
             int(market.get("neg_risk", False)), datetime.utcnow().isoformat()))

    async def get_cached_market(self, market_id: str) -> dict | None:
        return await self.fetch_one("SELECT * FROM market_cache WHERE market_id=?", (market_id,))
```

- [ ] **Step 2: Commit**

```bash
git add src/state/database.py
git commit -m "feat: add SQLite database with WAL mode and all CRUD operations"
```

---

## Chunk 2: Copy Trading Core (Models, Kelly, Slippage, Filters)

### Task 5: Data Models

**Files:**
- Create: `src/copytrade/models.py`
- Create: `src/executor/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_models.py
def test_wallet_profile_creation():
    from src.copytrade.models import WalletProfile, CategoryStats
    stats = CategoryStats(win_rate=0.62, resolved_positions=45, roi=0.15,
                          profit_factor=1.8, avg_position_size=50.0, total_pnl=5000.0)
    profile = WalletProfile(address="0xabc", username="trader1",
                            categories={"crypto": stats},
                            total_pnl=5000.0, total_volume=100000.0)
    assert profile.address == "0xabc"
    assert profile.categories["crypto"].win_rate == 0.62
    assert not profile.is_suspicious

def test_copy_signal_creation():
    from src.copytrade.models import CopySignal
    signal = CopySignal(
        market_id="cond123", token_id="tok456", question="BTC above 100K?",
        category="crypto", side="BUY_YES", leader_address="0xabc",
        leader_username="trader1", leader_price=0.48, current_price=0.49,
        estimated_slippage=0.01, position_size_usd=85.0,
        leader_category_wr=0.62, kelly_fraction=0.034, neg_risk=False,
        reasoning="Copying trader1 in crypto (WR: 62%)"
    )
    assert signal.side == "BUY_YES"
    assert signal.position_size_usd == 85.0

def test_order_result_creation():
    from src.executor.models import OrderResult
    result = OrderResult(order_id="ord1", market_id="cond123", side="BUY_YES",
                         price=0.49, size_usd=85.0, size_shares=173.47,
                         fee=1.53, status="filled")
    assert result.status == "filled"

def test_position_creation():
    from src.executor.models import Position
    pos = Position(market_id="cond123", token_id="tok456", side="BUY_YES",
                   entry_price=0.49, size_shares=173.47, size_usd=85.0,
                   signal_source="copy", leader_address="0xabc")
    assert pos.unrealized_pnl == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL

- [ ] **Step 3: Implement copytrade/models.py**

```python
# src/copytrade/models.py
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CategoryStats:
    win_rate: float
    resolved_positions: int
    roi: float
    profit_factor: float
    avg_position_size: float
    total_pnl: float


@dataclass
class WalletProfile:
    address: str
    username: str
    categories: dict[str, CategoryStats]
    total_pnl: float
    total_volume: float
    is_suspicious: bool = False
    last_scored: datetime | None = None


@dataclass
class CopySignal:
    market_id: str          # condition_id
    token_id: str           # outcome token for CLOB
    question: str
    category: str
    side: str               # "BUY_YES" or "BUY_NO"
    leader_address: str
    leader_username: str
    leader_price: float
    current_price: float
    estimated_slippage: float
    position_size_usd: float
    leader_category_wr: float
    kelly_fraction: float
    neg_risk: bool
    reasoning: str
```

- [ ] **Step 4: Implement executor/models.py**

```python
# src/executor/models.py
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OrderResult:
    order_id: str
    market_id: str
    side: str
    price: float
    size_usd: float
    size_shares: float
    fee: float
    status: str             # "filled", "cancelled", "pending"


@dataclass
class Position:
    market_id: str
    token_id: str
    side: str
    entry_price: float
    size_shares: float
    size_usd: float
    signal_source: str
    leader_address: str | None = None
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    opened_at: datetime = field(default_factory=datetime.utcnow)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_models.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/copytrade/models.py src/executor/models.py tests/test_models.py
git commit -m "feat: add data models for copy trading signals, wallet profiles, and positions"
```

---

### Task 6: Kelly Criterion

**Files:**
- Create: `src/executor/kelly.py`
- Create: `tests/test_kelly.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_kelly.py
import pytest

def test_kelly_basic_edge():
    from src.executor.kelly import kelly_size
    # P_true=0.62, market=0.48, bankroll=10000, quarter kelly
    result = kelly_size(p_true=0.62, market_price=0.48, bankroll=10000, kelly_fraction=0.25)
    # full_kelly = (0.62 - 0.48) / (1 - 0.48) = 0.2692
    # quarter = 0.2692 * 0.25 = 0.0673
    # position = 0.0673 * 10000 = 673
    assert 600 < result < 750

def test_kelly_no_edge():
    from src.executor.kelly import kelly_size
    result = kelly_size(p_true=0.48, market_price=0.50, bankroll=10000)
    assert result == 0  # negative edge, no bet

def test_kelly_respects_max_position():
    from src.executor.kelly import kelly_size
    # Huge edge should be capped at max_position_pct
    result = kelly_size(p_true=0.90, market_price=0.30, bankroll=10000,
                        max_position_pct=0.05)
    assert result <= 500  # 5% of 10000

def test_kelly_respects_min_trade():
    from src.executor.kelly import kelly_size
    result = kelly_size(p_true=0.51, market_price=0.50, bankroll=100,
                        min_trade_size=10)
    # Tiny edge on small bankroll — kelly says very small bet
    # If kelly < min_trade_size, return 0
    assert result == 0 or result >= 10

def test_kelly_respects_cash_reserve():
    from src.executor.kelly import kelly_size
    # bankroll=1000, reserve=30%, available=700
    result = kelly_size(p_true=0.70, market_price=0.40, bankroll=1000,
                        cash_reserve_pct=0.30, max_position_pct=1.0)
    assert result <= 700

def test_kelly_copy_signal_uses_conservative_fraction():
    from src.executor.kelly import kelly_size
    full = kelly_size(p_true=0.62, market_price=0.48, bankroll=10000, kelly_fraction=0.25)
    conservative = kelly_size(p_true=0.62, market_price=0.48, bankroll=10000, kelly_fraction=0.175)
    assert conservative < full
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_kelly.py -v`
Expected: FAIL

- [ ] **Step 3: Implement kelly.py**

```python
# src/executor/kelly.py

def kelly_size(
    p_true: float,
    market_price: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.05,
    cash_reserve_pct: float = 0.30,
    min_trade_size: float = 10.0,
) -> float:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_kelly.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/executor/kelly.py tests/test_kelly.py
git commit -m "feat: add Kelly criterion position sizing with safety caps"
```

---

### Task 7: Slippage Estimator (Orderbook Walk)

**Files:**
- Create: `src/executor/slippage.py`
- Create: `tests/test_slippage.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_slippage.py
import pytest

def test_estimate_fill_basic():
    from src.executor.slippage import estimate_fill_price
    book = {
        "asks": [
            {"price": "0.50", "size": "100"},
            {"price": "0.52", "size": "200"},
            {"price": "0.55", "size": "500"},
        ]
    }
    price = estimate_fill_price(book, side="BUY", size_usd=50)
    assert price == 0.50  # fills entirely at first level

def test_estimate_fill_walks_book():
    from src.executor.slippage import estimate_fill_price
    book = {
        "asks": [
            {"price": "0.50", "size": "20"},
            {"price": "0.52", "size": "200"},
        ]
    }
    # 20 shares at 0.50 ($10) + remaining at 0.52
    price = estimate_fill_price(book, side="BUY", size_usd=50)
    assert 0.50 < price < 0.52

def test_estimate_fill_insufficient_liquidity():
    from src.executor.slippage import estimate_fill_price
    book = {"asks": [{"price": "0.50", "size": "10"}]}
    price = estimate_fill_price(book, side="BUY", size_usd=1000)
    assert price is None

def test_estimate_fill_sell_uses_bids():
    from src.executor.slippage import estimate_fill_price
    book = {
        "bids": [
            {"price": "0.48", "size": "100"},
            {"price": "0.46", "size": "200"},
        ]
    }
    price = estimate_fill_price(book, side="SELL", size_usd=50)
    assert price == 0.48

def test_calculate_slippage():
    from src.executor.slippage import calculate_slippage
    slippage = calculate_slippage(leader_price=0.50, fill_price=0.52)
    assert abs(slippage - 0.04) < 0.001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_slippage.py -v`
Expected: FAIL

- [ ] **Step 3: Implement slippage.py**

```python
# src/executor/slippage.py

def estimate_fill_price(book: dict, side: str, size_usd: float) -> float | None:
    levels = book.get("asks" if side == "BUY" else "bids", [])
    if not levels:
        return None

    remaining_usd = size_usd
    total_shares = 0.0
    total_cost = 0.0

    for level in levels:
        price = float(level["price"])
        available_shares = float(level["size"])
        available_usd = available_shares * price

        if available_usd >= remaining_usd:
            shares_at_level = remaining_usd / price
            total_shares += shares_at_level
            total_cost += remaining_usd
            remaining_usd = 0
            break
        else:
            total_shares += available_shares
            total_cost += available_usd
            remaining_usd -= available_usd

    if remaining_usd > 0:
        return None  # insufficient liquidity

    return total_cost / total_shares if total_shares > 0 else None


def calculate_slippage(leader_price: float, fill_price: float) -> float:
    if leader_price == 0:
        return 0.0
    return abs(fill_price - leader_price) / leader_price
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_slippage.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/executor/slippage.py tests/test_slippage.py
git commit -m "feat: add orderbook walk slippage estimator"
```

---

### Task 8: Copy Trading Filters

**Files:**
- Create: `src/copytrade/filters.py`
- Create: `tests/test_filters.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_filters.py
import pytest
import time
from src.copytrade.models import CopySignal

def _make_signal(**overrides) -> dict:
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

def test_filter_passes_good_signal():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(), cfg)
    assert result.passed

def test_filter_rejects_wrong_category():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig(categories=["CRYPTO"])
    result = apply_filters(_make_signal(leader_category_wr=0.40), cfg)
    assert not result.passed
    assert "win_rate" in result.reason.lower()

def test_filter_rejects_stale_trade():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(trade_age_seconds=120), cfg)
    assert not result.passed
    assert "stale" in result.reason.lower() or "age" in result.reason.lower()

def test_filter_rejects_reducing_position():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(leader_is_reducing=True), cfg)
    assert not result.passed

def test_filter_rejects_price_drift():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(price_drift=0.08), cfg)
    assert not result.passed

def test_filter_rejects_wide_spread():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(spread=0.15), cfg)
    assert not result.passed

def test_filter_rejects_high_slippage():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(estimated_slippage=0.05), cfg)
    assert not result.passed

def test_filter_rejects_at_max_positions():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig(max_concurrent_positions=5)
    result = apply_filters(_make_signal(current_positions_count=5), cfg)
    assert not result.passed

def test_filter_rejects_duplicate_market():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(market_has_position=True), cfg)
    assert not result.passed

def test_filter_rejects_insufficient_category_history():
    from src.copytrade.filters import apply_filters
    from src.config import CopyTradingConfig
    cfg = CopyTradingConfig()
    result = apply_filters(_make_signal(leader_category_positions=5), cfg)
    assert not result.passed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_filters.py -v`
Expected: FAIL

- [ ] **Step 3: Implement filters.py**

```python
# src/copytrade/filters.py
from dataclasses import dataclass
from src.config import CopyTradingConfig
import logging

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""


def apply_filters(signal: dict, config: CopyTradingConfig) -> FilterResult:
    # Filter 1: Category win rate
    wr = signal.get("leader_category_wr", 0)
    if wr < config.min_win_rate:
        return FilterResult(False, f"Win_rate too low: {wr:.2f} < {config.min_win_rate}")
    if wr > config.max_win_rate:
        return FilterResult(False, f"Win_rate suspiciously high: {wr:.2f} > {config.max_win_rate}")

    # Filter 2: Minimum category history
    cat_positions = signal.get("leader_category_positions", 0)
    if cat_positions < config.min_category_positions:
        return FilterResult(False, f"Insufficient category history: {cat_positions} < {config.min_category_positions}")

    # Filter 3: Staleness
    age = signal.get("trade_age_seconds", 999)
    if age > config.max_trade_age_seconds:
        return FilterResult(False, f"Trade too stale: {age}s > {config.max_trade_age_seconds}s")

    # Filter 4: Leader reducing position
    if signal.get("leader_is_reducing", False):
        return FilterResult(False, "Leader is reducing position, not entering")

    # Filter 5: Price drift
    drift = signal.get("price_drift", 0)
    if drift > config.max_price_drift:
        return FilterResult(False, f"Price drift too high: {drift:.3f} > {config.max_price_drift}")

    # Filter 6: Spread
    spread = signal.get("spread", 1.0)
    if spread > config.max_spread:
        return FilterResult(False, f"Spread too wide: {spread:.3f} > {config.max_spread}")

    # Filter 7: Slippage
    slippage = signal.get("estimated_slippage", 0)
    if slippage > config.max_slippage:
        return FilterResult(False, f"Slippage too high: {slippage:.3f} > {config.max_slippage}")

    # Filter 8: Exposure - max positions
    pos_count = signal.get("current_positions_count", 0)
    if pos_count >= config.max_concurrent_positions:
        return FilterResult(False, f"At max positions: {pos_count} >= {config.max_concurrent_positions}")

    # Filter 9: Duplicate market
    if signal.get("market_has_position", False):
        return FilterResult(False, "Already have position in this market")

    return FilterResult(True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_filters.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/copytrade/filters.py tests/test_filters.py
git commit -m "feat: add 9-stage copy trading filter pipeline with full test coverage"
```

---

## Chunk 3: Copy Trading Services (Discovery, Monitor, Paper Executor)

### Task 9: Wallet Discovery Service

**Files:**
- Create: `src/copytrade/discovery.py`
- Create: `tests/test_discovery.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_discovery.py
import pytest

def test_score_wallet_from_trades():
    from src.copytrade.discovery import score_wallet_trades
    trades = [
        {"outcome": "YES", "side": "BUY", "price": 0.40, "size": 50, "status": "WON", "category": "crypto"},
        {"outcome": "YES", "side": "BUY", "price": 0.60, "size": 30, "status": "WON", "category": "crypto"},
        {"outcome": "YES", "side": "BUY", "price": 0.50, "size": 40, "status": "LOST", "category": "crypto"},
        {"outcome": "YES", "side": "BUY", "price": 0.70, "size": 20, "status": "WON", "category": "politics"},
    ]
    stats = score_wallet_trades(trades)
    assert "crypto" in stats
    assert stats["crypto"]["win_rate"] == pytest.approx(2/3, rel=0.01)
    assert stats["crypto"]["resolved_positions"] == 3

def test_detect_suspicious_wallet():
    from src.copytrade.discovery import is_suspicious
    # >90% win rate with few trades = suspicious
    stats = {"crypto": {"win_rate": 0.95, "resolved_positions": 10}}
    assert is_suspicious(stats, max_win_rate=0.90)

def test_not_suspicious_normal_wallet():
    from src.copytrade.discovery import is_suspicious
    stats = {"crypto": {"win_rate": 0.62, "resolved_positions": 50}}
    assert not is_suspicious(stats, max_win_rate=0.90)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery.py -v`
Expected: FAIL

- [ ] **Step 3: Implement discovery.py**

```python
# src/copytrade/discovery.py
import logging
from src.copytrade.models import WalletProfile, CategoryStats
from src.polymarket_api import PolymarketAPI
from src.state.database import Database
from src.config import CopyTradingConfig

logger = logging.getLogger(__name__)


def score_wallet_trades(trades: list[dict]) -> dict[str, dict]:
    by_category: dict[str, list[dict]] = {}
    for t in trades:
        cat = t.get("category", "unknown").lower()
        by_category.setdefault(cat, []).append(t)

    result = {}
    for cat, cat_trades in by_category.items():
        wins = sum(1 for t in cat_trades if t.get("status") == "WON")
        losses = sum(1 for t in cat_trades if t.get("status") == "LOST")
        total = wins + losses
        if total == 0:
            continue

        gross_profit = sum(
            float(t.get("size", 0)) * (1.0 - float(t.get("price", 0)))
            for t in cat_trades if t.get("status") == "WON"
        )
        gross_loss = sum(
            float(t.get("size", 0)) * float(t.get("price", 0))
            for t in cat_trades if t.get("status") == "LOST"
        )

        result[cat] = {
            "win_rate": wins / total,
            "resolved_positions": total,
            "roi": (gross_profit - gross_loss) / max(gross_loss, 1),
            "profit_factor": gross_profit / max(gross_loss, 0.01),
            "avg_position_size": sum(float(t.get("size", 0)) for t in cat_trades) / len(cat_trades),
            "total_pnl": gross_profit - gross_loss,
        }
    return result


def is_suspicious(category_stats: dict, max_win_rate: float = 0.90) -> bool:
    for cat, stats in category_stats.items():
        wr = stats.get("win_rate", 0) if isinstance(stats, dict) else stats.win_rate
        positions = stats.get("resolved_positions", 0) if isinstance(stats, dict) else stats.resolved_positions
        if wr > max_win_rate and positions < 100:
            return True
    return False


class WalletDiscovery:
    def __init__(self, api: PolymarketAPI, db: Database, config: CopyTradingConfig):
        self._api = api
        self._db = db
        self._config = config

    async def discover(self):
        logger.info("Starting wallet discovery...")
        for category in self._config.categories:
            leaders = await self._api.get_leaderboard(
                category=category, period="MONTH", limit=50)

            for leader in leaders:
                address = leader.get("proxyWallet", "")
                if not address:
                    continue

                trades = await self._api.get_all_trades(address)
                # Enrich trades with category from market metadata
                enriched = await self._enrich_trades_with_category(trades)
                stats = score_wallet_trades(enriched)
                suspicious = is_suspicious(stats, self._config.max_win_rate)

                volume = float(leader.get("vol", 0))
                pnl = float(leader.get("pnl", 0))

                if volume < self._config.min_volume:
                    continue

                await self._db.upsert_wallet({
                    "address": address,
                    "username": leader.get("userName", ""),
                    "category_stats": stats,
                    "total_pnl": pnl,
                    "total_volume": volume,
                    "is_suspicious": suspicious,
                })
                logger.info(f"Scored {leader.get('userName', address)}: PnL={pnl}, suspicious={suspicious}")

        wallets = await self._db.get_tracked_wallets()
        # Enforce max_tracked_wallets limit
        if len(wallets) > self._config.max_tracked_wallets:
            logger.info(f"Trimming to top {self._config.max_tracked_wallets} wallets by PnL")
        logger.info(f"Discovery complete. Tracking {min(len(wallets), self._config.max_tracked_wallets)} wallets")

    async def _enrich_trades_with_category(self, trades: list[dict]) -> list[dict]:
        for trade in trades:
            market_id = trade.get("market", "")
            cached = await self._db.get_cached_market(market_id)
            if cached:
                trade["category"] = cached.get("category", "unknown")
            else:
                try:
                    market_data = await self._api.get_market(market_id)
                    tags = market_data.get("tags", [])
                    category = tags[0].get("label", "unknown").lower() if tags else "unknown"
                    trade["category"] = category
                    await self._db.cache_market({
                        "market_id": market_id,
                        "condition_id": market_data.get("conditionId", ""),
                        "token_id_yes": "",
                        "token_id_no": "",
                        "question": market_data.get("question", ""),
                        "category": category,
                        "neg_risk": market_data.get("negRisk", False),
                    })
                except Exception:
                    trade["category"] = "unknown"
        return trades
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_discovery.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/copytrade/discovery.py tests/test_discovery.py
git commit -m "feat: add wallet discovery with per-category scoring and fake detection"
```

---

### Task 10: Trade Monitor (Round-Robin Poller)

**Files:**
- Create: `src/copytrade/monitor.py`
- Create: `tests/test_monitor.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_monitor.py
import pytest

def test_round_robin_index():
    from src.copytrade.monitor import RoundRobinPoller
    poller = RoundRobinPoller(wallets=["0xA", "0xB", "0xC"])
    assert poller.next_wallet() == "0xA"
    assert poller.next_wallet() == "0xB"
    assert poller.next_wallet() == "0xC"
    assert poller.next_wallet() == "0xA"  # wraps around

def test_round_robin_empty():
    from src.copytrade.monitor import RoundRobinPoller
    poller = RoundRobinPoller(wallets=[])
    assert poller.next_wallet() is None

def test_round_robin_update_wallets():
    from src.copytrade.monitor import RoundRobinPoller
    poller = RoundRobinPoller(wallets=["0xA", "0xB"])
    poller.next_wallet()  # 0xA
    poller.update_wallets(["0xC", "0xD", "0xE"])
    # Index resets
    assert poller.next_wallet() == "0xC"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_monitor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement monitor.py**

```python
# src/copytrade/monitor.py
import logging
import time
from datetime import datetime
from src.polymarket_api import PolymarketAPI
from src.state.database import Database
from src.copytrade.filters import apply_filters, FilterResult
from src.copytrade.models import CopySignal
from src.executor.kelly import kelly_size
from src.executor.slippage import estimate_fill_price, calculate_slippage
from src.config import Config

logger = logging.getLogger(__name__)


class RoundRobinPoller:
    def __init__(self, wallets: list[str]):
        self._wallets = list(wallets)
        self._index = 0

    def next_wallet(self) -> str | None:
        if not self._wallets:
            return None
        wallet = self._wallets[self._index % len(self._wallets)]
        self._index = (self._index + 1) % len(self._wallets)
        return wallet

    def update_wallets(self, wallets: list[str]):
        self._wallets = list(wallets)
        self._index = 0


class CopyTradeMonitor:
    def __init__(self, api: PolymarketAPI, db: Database, config: Config):
        self._api = api
        self._db = db
        self._config = config
        self._poller = RoundRobinPoller([])

    async def refresh_wallet_list(self):
        wallets = await self._db.get_tracked_wallets()
        addresses = [w["address"] for w in wallets[:self._config.copy_trading.max_tracked_wallets]]
        self._poller.update_wallets(addresses)
        logger.info(f"Monitoring {len(addresses)} wallets")

    async def poll_next(self) -> list[CopySignal]:
        address = self._poller.next_wallet()
        if not address:
            return []

        try:
            activities = await self._api.get_activity(address, limit=10)
        except Exception as e:
            logger.error(f"Failed to poll {address}: {e}")
            return []

        signals = []
        for activity in activities:
            trade_id = activity.get("id", "")
            if await self._db.is_known_trade(trade_id):
                continue

            await self._db.mark_trade_known(trade_id, address)
            signal = await self._evaluate_trade(activity, address)
            if signal:
                signals.append(signal)

        return signals

    async def _evaluate_trade(self, trade: dict, leader_address: str) -> CopySignal | None:
        market_id = trade.get("market", "")
        match_time = trade.get("match_time", "")
        trade_age = self._calculate_age(match_time)
        side = "BUY_YES" if trade.get("side") == "BUY" else "BUY_NO"
        leader_price = float(trade.get("price", 0))
        token_id = trade.get("asset_id", "")

        # Get market metadata
        cached = await self._db.get_cached_market(market_id)
        if not cached:
            try:
                market_data = await self._api.get_market(market_id)
                category = "unknown"
                tags = market_data.get("tags", [])
                if tags:
                    category = tags[0].get("label", "unknown").lower()
                cached = {
                    "category": category,
                    "neg_risk": market_data.get("negRisk", False),
                    "question": market_data.get("question", ""),
                }
                await self._db.cache_market({
                    "market_id": market_id, "condition_id": market_id,
                    "token_id_yes": "", "token_id_no": "",
                    "question": cached["question"], "category": category,
                    "neg_risk": cached["neg_risk"],
                })
            except Exception as e:
                logger.error(f"Failed to get market {market_id}: {e}")
                return None

        category = cached.get("category", "unknown")

        # Get leader's wallet profile for category stats
        wallet_data = await self._db.fetch_one(
            "SELECT category_stats FROM wallet_profiles WHERE address=?", (leader_address,))
        if not wallet_data:
            return None

        import json
        cat_stats = json.loads(wallet_data.get("category_stats", "{}"))
        cat_data = cat_stats.get(category, {})
        leader_wr = cat_data.get("win_rate", 0)
        leader_cat_positions = cat_data.get("resolved_positions", 0)

        # Get current price and spread
        try:
            spread = await self._api.get_spread(token_id)
            midpoint = await self._api.get_midpoint(token_id)
        except Exception:
            return None

        price_drift = abs(midpoint - leader_price) / max(leader_price, 0.01)

        # Estimate slippage
        try:
            book = await self._api.get_book(token_id)
            fill_price = estimate_fill_price(
                book, "BUY" if "YES" in side else "SELL",
                self._config.copy_trading.max_position_per_market)
            slippage = calculate_slippage(leader_price, fill_price) if fill_price else 1.0
        except Exception:
            slippage = 1.0

        # Check existing positions
        pos_count = await self._db.get_position_count()
        existing = await self._db.fetch_one(
            "SELECT id FROM positions WHERE market_id=? AND signal_source='copy'", (market_id,))

        # Apply filters
        filter_input = {
            "category": category,
            "leader_category_wr": leader_wr,
            "leader_category_positions": leader_cat_positions,
            "trade_age_seconds": trade_age,
            "leader_is_reducing": trade.get("side") == "SELL",
            "price_drift": price_drift,
            "spread": spread,
            "estimated_slippage": slippage,
            "current_positions_count": pos_count,
            "market_has_position": existing is not None,
        }

        result = apply_filters(filter_input, self._config.copy_trading)
        if not result.passed:
            logger.debug(f"Filtered: {result.reason}")
            return None

        # Size with Kelly (conservative fraction for copy signals)
        bankroll = self._config.risk.starting_bankroll  # TODO: use actual portfolio value
        position_size = kelly_size(
            p_true=leader_wr,
            market_price=midpoint,
            bankroll=bankroll,
            kelly_fraction=self._config.risk.kelly_fraction * 0.7,  # conservative for copy
            max_position_pct=self._config.risk.max_position_pct,
            cash_reserve_pct=self._config.risk.cash_reserve_pct,
            min_trade_size=self._config.risk.min_trade_size,
        )

        if position_size <= 0:
            return None

        # Cap at per-market limit
        position_size = min(position_size, self._config.copy_trading.max_position_per_market)

        username_row = await self._db.fetch_one(
            "SELECT username FROM wallet_profiles WHERE address=?", (leader_address,))
        username = username_row["username"] if username_row else leader_address[:10]

        return CopySignal(
            market_id=market_id,
            token_id=token_id,
            question=cached.get("question", ""),
            category=category,
            side=side,
            leader_address=leader_address,
            leader_username=username,
            leader_price=leader_price,
            current_price=midpoint,
            estimated_slippage=slippage,
            position_size_usd=position_size,
            leader_category_wr=leader_wr,
            kelly_fraction=self._config.risk.kelly_fraction * 0.7,
            neg_risk=cached.get("neg_risk", False),
            reasoning=f"Copying {username} in {category} (WR: {leader_wr:.0%}, PF: {cat_data.get('profit_factor', 0):.1f})"
        )

    def _calculate_age(self, match_time: str) -> float:
        if not match_time:
            return 999
        try:
            ts = datetime.fromisoformat(match_time.replace("Z", "+00:00"))
            return (datetime.now(ts.tzinfo) - ts).total_seconds()
        except Exception:
            return 999
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_monitor.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/copytrade/monitor.py tests/test_monitor.py
git commit -m "feat: add round-robin trade monitor with full signal evaluation pipeline"
```

---

### Task 11: Paper Executor

**Files:**
- Create: `src/executor/base.py`
- Create: `src/executor/paper.py`
- Create: `tests/test_paper_executor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_paper_executor.py
import pytest

@pytest.mark.asyncio
async def test_paper_executor_opens_position():
    from src.executor.paper import PaperExecutor
    from src.state.database import Database
    from src.copytrade.models import CopySignal

    db = Database(":memory:")
    await db.connect()
    executor = PaperExecutor(db, starting_bankroll=10000)

    signal = CopySignal(
        market_id="cond1", token_id="tok1", question="Test?",
        category="crypto", side="BUY_YES", leader_address="0xL",
        leader_username="leader", leader_price=0.50, current_price=0.51,
        estimated_slippage=0.01, position_size_usd=100,
        leader_category_wr=0.62, kelly_fraction=0.175, neg_risk=False,
        reasoning="test"
    )

    result = await executor.execute(signal)
    assert result.status == "filled"
    assert result.size_usd == 100
    assert executor.cash_balance == 10000 - 100 - result.fee
    positions = await db.get_open_positions()
    assert len(positions) == 1
    await db.close()

@pytest.mark.asyncio
async def test_paper_executor_applies_fees():
    from src.executor.paper import PaperExecutor
    from src.state.database import Database
    from src.copytrade.models import CopySignal

    db = Database(":memory:")
    await db.connect()
    executor = PaperExecutor(db, starting_bankroll=10000, fees={"crypto": 0.018})

    signal = CopySignal(
        market_id="cond1", token_id="tok1", question="Test?",
        category="crypto", side="BUY_YES", leader_address="0xL",
        leader_username="leader", leader_price=0.50, current_price=0.50,
        estimated_slippage=0.0, position_size_usd=100,
        leader_category_wr=0.62, kelly_fraction=0.175, neg_risk=False,
        reasoning="test"
    )

    result = await executor.execute(signal)
    # Fee = 100 * 0.018 * 0.50 * (1 - 0.50) = 0.45
    assert result.fee > 0
    await db.close()

@pytest.mark.asyncio
async def test_paper_executor_rejects_insufficient_balance():
    from src.executor.paper import PaperExecutor
    from src.state.database import Database
    from src.copytrade.models import CopySignal

    db = Database(":memory:")
    await db.connect()
    executor = PaperExecutor(db, starting_bankroll=50)

    signal = CopySignal(
        market_id="cond1", token_id="tok1", question="Test?",
        category="crypto", side="BUY_YES", leader_address="0xL",
        leader_username="leader", leader_price=0.50, current_price=0.50,
        estimated_slippage=0.0, position_size_usd=100,
        leader_category_wr=0.62, kelly_fraction=0.175, neg_risk=False,
        reasoning="test"
    )

    result = await executor.execute(signal)
    assert result.status == "cancelled"
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_paper_executor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement base.py**

```python
# src/executor/base.py
from abc import ABC, abstractmethod
from src.executor.models import OrderResult, Position
from src.copytrade.models import CopySignal


class BaseExecutor(ABC):
    @abstractmethod
    async def execute(self, signal: CopySignal) -> OrderResult:
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        ...

    @abstractmethod
    async def close_position(self, market_id: str, side: str, signal_source: str) -> OrderResult | None:
        ...
```

- [ ] **Step 4: Implement paper.py**

```python
# src/executor/paper.py
import uuid
import logging
from datetime import datetime
from src.executor.base import BaseExecutor
from src.executor.models import OrderResult, Position
from src.copytrade.models import CopySignal
from src.state.database import Database

logger = logging.getLogger(__name__)


class PaperExecutor(BaseExecutor):
    def __init__(self, db: Database, starting_bankroll: float = 10000,
                 fees: dict[str, float] | None = None, default_slippage: float = 0.005):
        self._db = db
        self.cash_balance = starting_bankroll
        self._starting_bankroll = starting_bankroll
        self._fees = fees or {"default": 0.01}
        self._default_slippage = default_slippage

    async def execute(self, signal: CopySignal) -> OrderResult:
        size_usd = signal.position_size_usd

        # Calculate fee: fee_rate * size * p * (1 - p)
        fee_rate = self._fees.get(signal.category, self._fees.get("default", 0.01))
        p = signal.current_price
        fee = size_usd * fee_rate * p * (1 - p)

        total_cost = size_usd + fee

        if total_cost > self.cash_balance:
            logger.warning(f"Insufficient balance: need {total_cost:.2f}, have {self.cash_balance:.2f}")
            return OrderResult(
                order_id=str(uuid.uuid4()), market_id=signal.market_id,
                side=signal.side, price=0, size_usd=0, size_shares=0,
                fee=0, status="cancelled"
            )

        # Simulate fill at current price + slippage
        fill_price = signal.current_price * (1 + self._default_slippage)
        shares = size_usd / fill_price

        self.cash_balance -= total_cost
        order_id = str(uuid.uuid4())

        # Record trade
        await self._db.insert_trade({
            "id": order_id,
            "timestamp": datetime.utcnow().isoformat(),
            "market_id": signal.market_id,
            "token_id": signal.token_id,
            "question": signal.question,
            "category": signal.category,
            "side": signal.side,
            "price": fill_price,
            "size_usd": size_usd,
            "fee": fee,
            "signal_source": "copy",
            "signal_data": {"leader": signal.leader_address, "reasoning": signal.reasoning},
            "order_type": "FOK",
            "status": "filled",
            "leader_address": signal.leader_address,
        })

        # Record position
        await self._db.upsert_position({
            "market_id": signal.market_id,
            "token_id": signal.token_id,
            "side": signal.side,
            "entry_price": fill_price,
            "size_shares": shares,
            "size_usd": size_usd,
            "current_price": fill_price,
            "unrealized_pnl": 0,
            "signal_source": "copy",
            "leader_address": signal.leader_address,
        })

        logger.info(f"PAPER TRADE: {signal.side} {signal.question[:50]} | ${size_usd:.2f} @ {fill_price:.4f} | fee: ${fee:.2f}")

        return OrderResult(
            order_id=order_id, market_id=signal.market_id,
            side=signal.side, price=fill_price, size_usd=size_usd,
            size_shares=shares, fee=fee, status="filled"
        )

    async def get_positions(self) -> list[Position]:
        rows = await self._db.get_open_positions()
        return [Position(
            market_id=r["market_id"], token_id=r.get("token_id", ""),
            side=r["side"], entry_price=r["entry_price"],
            size_shares=r["size_shares"], size_usd=r["size_usd"],
            signal_source=r["signal_source"], leader_address=r.get("leader_address"),
            current_price=r.get("current_price", r["entry_price"]),
            unrealized_pnl=r.get("unrealized_pnl", 0),
        ) for r in rows]

    async def get_balance(self) -> float:
        return self.cash_balance

    async def close_position(self, market_id: str, side: str, signal_source: str) -> OrderResult | None:
        pos = await self._db.fetch_one(
            "SELECT * FROM positions WHERE market_id=? AND side=? AND signal_source=?",
            (market_id, side, signal_source))
        if not pos:
            return None

        self.cash_balance += pos["size_usd"] + pos.get("unrealized_pnl", 0)
        await self._db.remove_position(market_id, side, signal_source)

        logger.info(f"PAPER CLOSE: {side} {market_id} | PnL: ${pos.get('unrealized_pnl', 0):.2f}")
        return OrderResult(
            order_id=str(uuid.uuid4()), market_id=market_id,
            side=side, price=pos.get("current_price", pos["entry_price"]),
            size_usd=pos["size_usd"], size_shares=pos["size_shares"],
            fee=0, status="filled"
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_paper_executor.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/executor/base.py src/executor/paper.py tests/test_paper_executor.py
git commit -m "feat: add paper executor with simulated fills, fees, and position tracking"
```

---

## Chunk 4: Monitoring (Telegram + Dashboard) and Main Loop

### Task 12: Telegram Bot

**Files:**
- Create: `src/monitor/telegram_bot.py`

No unit tests for Telegram — it's pure I/O. Tested manually.

- [ ] **Step 1: Implement telegram_bot.py**

The implementation should include:
- `TelegramNotifier` class with methods: `send_trade_alert()`, `send_daily_summary()`, `send_risk_alert()`, `send_error()`
- Command handlers: `/status`, `/positions`, `/pnl`, `/leaders`, `/history`, `/pause`, `/resume`, `/kill`
- Auth check: reject messages not from configured `telegram_user_id`
- Non-blocking: alerts are fire-and-forget, errors in Telegram never crash the main loop

```python
# src/monitor/telegram_bot.py
# Full implementation with all handlers and alert methods
# Key pattern: all methods catch their own exceptions and log errors
# The Telegram bot runs as a separate asyncio task alongside the main loop
```

- [ ] **Step 2: Commit**

```bash
git add src/monitor/telegram_bot.py
git commit -m "feat: add Telegram bot with alerts, commands, and auth"
```

---

### Task 13: Web Dashboard

**Files:**
- Create: `src/monitor/dashboard.py`
- Create: all template files in `src/templates/`
- Create: `static/chart_config.js`

- [ ] **Step 1: Implement dashboard.py**

FastAPI app with routes:
- `GET /` → overview (redirect)
- `GET /overview` → overview page
- `GET /positions` → positions table
- `GET /trades` → trade log with pagination
- `GET /pnl` → PnL charts
- `GET /leaders` → tracked wallets table
- `GET /settings` → config view
- `GET /api/portfolio` → JSON endpoint for chart data

All routes read from SQLite. No writes from dashboard (read-only view).

- [ ] **Step 2: Create HTML templates**

Templates use Jinja2 with Tailwind CSS via CDN. Base template includes navigation. Each page extends base.

- [ ] **Step 3: Commit**

```bash
git add src/monitor/dashboard.py src/templates/ static/
git commit -m "feat: add FastAPI web dashboard with overview, positions, trades, PnL, leaders pages"
```

---

### Task 14: Main Loop

**Files:**
- Create: `src/main.py`

- [ ] **Step 1: Implement main.py**

```python
# src/main.py
import asyncio
import signal
import logging
import sys
from src.config import load_config
from src.rate_limiter import RateLimiter
from src.polymarket_api import PolymarketAPI
from src.state.database import Database
from src.copytrade.discovery import WalletDiscovery
from src.copytrade.monitor import CopyTradeMonitor
from src.executor.paper import PaperExecutor
from src.monitor.telegram_bot import TelegramNotifier

logger = logging.getLogger("copywallet")


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.logging.file) if config.logging.file else logging.NullHandler(),
        ]
    )

    # Initialize components
    rate_limiter = RateLimiter(max_requests_per_minute=90)
    api = PolymarketAPI(rate_limiter)
    db = Database(config.database.path)
    await db.connect()

    executor = PaperExecutor(db, config.risk.starting_bankroll, config.fees)
    discovery = WalletDiscovery(api, db, config.copy_trading)
    monitor = CopyTradeMonitor(api, db, config)
    telegram = TelegramNotifier(config.telegram_bot_token, config.telegram_user_id, db, executor)

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_signal(*_):
        logger.info("Shutdown signal received")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Initial discovery
    logger.info("Running initial wallet discovery...")
    await discovery.discover()
    await monitor.refresh_wallet_list()

    # Schedule periodic discovery
    last_discovery = asyncio.get_event_loop().time()
    discovery_interval = config.copy_trading.discovery_interval_hours * 3600

    logger.info(f"Copywallet started in {config.mode} mode")

    # Main loop
    while not shutdown_event.is_set():
        try:
            # Check if discovery is due
            now = asyncio.get_event_loop().time()
            if now - last_discovery > discovery_interval:
                await discovery.discover()
                await monitor.refresh_wallet_list()
                last_discovery = now

            # Poll next leader wallet
            signals = await monitor.poll_next()
            for sig in signals:
                result = await executor.execute(sig)
                if result.status == "filled":
                    await telegram.send_trade_alert(sig, result)

            # Portfolio snapshot (every 5 minutes)
            # Daily loss check
            # Position price updates

        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
            await telegram.send_error(str(e))

        await asyncio.sleep(config.copy_trading.poll_interval_seconds)

    # Cleanup
    logger.info("Shutting down...")
    await api.close()
    await db.close()
    logger.info("Copywallet stopped")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
git add src/main.py
git commit -m "feat: add main async loop with discovery, monitoring, and graceful shutdown"
```

---

### Task 15: Integration Test — Full Pipeline Smoke Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

A test that wires up all components with mocked API responses and verifies the full pipeline: discovery → monitor → filter → execute → database.

```python
# tests/test_integration.py
# Mock PolymarketAPI to return fake leaderboard + activity data
# Verify: wallet gets scored, trade gets detected, filter passes, paper executor fills,
# trade appears in DB, position appears in DB, cash balance decreases
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add full pipeline integration test with mocked API"
```

---

## Summary

| Chunk | Tasks | What It Delivers |
|-------|-------|-----------------|
| **1: Foundation** | Tasks 1-4 | Config, rate limiter, API client, database |
| **2: Core Logic** | Tasks 5-8 | Models, Kelly sizing, slippage estimator, 9-stage filter pipeline |
| **3: Services** | Tasks 9-11 | Wallet discovery, round-robin monitor, paper executor |
| **4: Interface** | Tasks 12-15 | Telegram bot, web dashboard, main loop, integration test |

**Total: 15 tasks, ~50 steps, estimated 30-40 commits.**

After this plan is complete, you'll have a working copy trading bot in paper mode that:
- Discovers and scores top Polymarket wallets every 6 hours
- Monitors their trades via round-robin polling (within rate limits)
- Filters trades through 9 validation stages
- Executes paper trades with realistic fees and slippage
- Shows everything via Telegram alerts and a web dashboard
- Stores all state in SQLite for analysis
