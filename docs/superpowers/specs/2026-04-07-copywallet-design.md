# Copywallet Design Specification

**Date**: 2026-04-07
**Status**: Draft
**Author**: Claude + User

## 1. Purpose

Copywallet is a fully autonomous Polymarket trading bot. It identifies profitable wallets, filters their trades by category dominance, and mirrors positions with mathematically-optimal sizing. A secondary Claude AI probability engine can be enabled as an additional signal source.

The system starts in paper trading mode and graduates to live trading after meeting quantitative performance criteria.

## 2. Goals

- **Primary**: Build a copy trading system that follows proven wallets with intelligent category filtering
- **Secondary**: Add Claude API probability estimation as a second signal source
- **Non-goal**: Market making (may be added in a future phase)
- **Non-goal**: Multi-venue arbitrage (Polymarket only for now)

## 3. Phased Delivery

| Phase | Scope | Depends On |
|-------|-------|------------|
| **Phase 1** | Copy Trading + Paper Executor + Telegram + Dashboard | Nothing |
| **Phase 2** | Claude Brain (EV + Kelly + Bayes) as second signal source | Phase 1 stable |
| **Phase 3** | Both strategies running together + live trading | Phase 1 + 2 validated |

This spec covers the full design. Implementation follows the phase order.

## 4. System Architecture

### 4.1 High-Level Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      COPYWALLET BOT                          │
│                                                              │
│  SIGNAL SOURCES              SHARED INFRASTRUCTURE           │
│  ┌──────────────┐           ┌──────────────────┐            │
│  │ COPY TRADING │──┐        │    EXECUTOR      │            │
│  │ (Phase 1)    │  │        │                  │            │
│  │ Discovery    │  │        │ Paper: simulated │            │
│  │ Monitor      │  ├──────→ │ Live: py-clob    │            │
│  │ Filter       │  │        │                  │            │
│  └──────────────┘  │        └────────┬─────────┘            │
│  ┌──────────────┐  │               │                        │
│  │ CLAUDE BRAIN │──┘               ▼                        │
│  │ (Phase 2)    │        ┌──────────────────┐               │
│  │ Scanner      │        │     STATE        │               │
│  │ Scorer       │        │                  │               │
│  │ EV/Kelly     │        │ SQLite DB        │               │
│  └──────────────┘        │ Positions/Trades │               │
│                          │ PnL/Portfolio    │               │
│                          └────────┬─────────┘               │
│                                  │                          │
│                       ┌──────────┴──────────┐               │
│                       ▼                     ▼               │
│                ┌────────────┐       ┌──────────────┐        │
│                │  TELEGRAM  │       │  DASHBOARD   │        │
│                │  BOT       │       │  (FastAPI)   │        │
│                └────────────┘       └──────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Main Loop (async)

```python
async def main_loop():
    while True:
        # Copy trading signals (Phase 1)
        if config.copy_trading.enabled:
            copy_signals = await copy_monitor.check_leaders()
            for signal in copy_signals:
                await executor.execute(signal)

        # Claude brain signals (Phase 2)
        if config.claude_brain.enabled:
            markets = await scanner.fetch_markets()
            for market in markets:
                signal = await brain.evaluate(market)
                if signal.action != "HOLD":
                    await executor.execute(signal)

        # Housekeeping
        await state.update_portfolio_snapshot()
        await executor.check_resolved_markets()
        await executor.enforce_risk_limits()

        await asyncio.sleep(config.scan_interval)
```

## 5. Module Specifications

### 5.1 Copy Trading Module (Phase 1)

#### 5.1.1 Wallet Discovery Service

**Trigger**: Runs every 6 hours.

**Process**:
1. Fetch top 50 wallets per category from Polymarket Leaderboard API
   - `GET /leaderboard?category={cat}&period=MONTH&orderBy=PNL&limit=50`
2. For each wallet, fetch full trade history (paginated, 100 per page)
   - `GET /trades?user={proxy_wallet}&limit=100&offset={n}`
3. For each trade, look up market category via Gamma API
   - `GET /markets/{conditionId}` → extract `tags` array
4. Calculate per-category stats: win rate, ROI, profit factor
5. Filter out fake/manipulated wallets (WR >90%, stat padding, wash trading)
6. Store scored profiles in SQLite

**Output**: `WalletProfile` objects with per-category `CategoryStats`.

**Data Models**:

```python
@dataclass
class WalletProfile:
    address: str               # proxy wallet address
    username: str
    categories: dict[str, CategoryStats]
    total_pnl: float
    total_volume: float
    is_suspicious: bool        # detected fake patterns
    last_scored: datetime
    
@dataclass
class CategoryStats:
    win_rate: float            # target: 0.55-0.67
    resolved_positions: int    # minimum: 20
    roi: float                 # return on investment
    profit_factor: float       # gross_profit / gross_loss, target: >1.5
    avg_position_size: float   # must be in copyable range
    total_pnl: float
```

#### 5.1.2 Trade Monitor Service

**Trigger**: Polls every 2 seconds continuously.

**Process**:
1. For each tracked wallet, poll activity endpoint
   - `GET /activity?user={proxy}&type=TRADE&sortBy=TIMESTAMP&sortDirection=DESC&limit=10`
2. Compare against known trade IDs to find new trades
3. For each new trade, enrich with market metadata and current prices
4. Pass through filtering pipeline
5. Output validated `CopySignal` to Executor

**Filtering Pipeline** (in order):

| # | Filter | Check | Reject If |
|---|--------|-------|-----------|
| 1 | Category | Market category vs wallet's strong categories | Category not whitelisted |
| 2 | Staleness | `now - trade.match_time` | >60 seconds old |
| 3 | Direction | Is leader reducing or increasing position? | Reducing (selling existing) |
| 4 | Price drift | `abs(current_price - leader_price) / leader_price` | >5% drift |
| 5 | Spread | Current bid-ask spread from CLOB API | >$0.10 |
| 6 | Slippage | Estimated fill via `/estimate-fill` | >2% worse than leader |
| 7 | Exposure | Current positions count and total exposure | At max limits |

**Data Model**:

```python
@dataclass
class CopySignal:
    market_id: str
    question: str
    category: str
    side: str                  # "BUY_YES" or "BUY_NO"
    leader_address: str
    leader_username: str
    leader_price: float        # price the leader got
    current_price: float       # price we'd get
    estimated_slippage: float
    position_size_usd: float   # Kelly-sized, not leader's size
    leader_category_wr: float  # win rate used as probability input
    kelly_fraction: float
    reasoning: str             # "Copying {username} in crypto (WR: 62%, PF: 1.8)"
```

#### 5.1.3 Leader Exit Tracking

When a leader sells a position we've copied:
1. Detect sell activity via the same polling mechanism
2. Validate: is this a full exit or partial reduction?
3. If full exit → sell our entire position
4. If partial → reduce proportionally
5. If our position is profitable at >90¢ (near resolution) → hold instead of selling

### 5.2 Market Scanner

**Purpose**: Fetch and filter active markets for the Claude Brain (Phase 2) and for enriching copy trade signals (Phase 1).

**Data Sources**:
- Gamma API: market questions, categories, token IDs, end dates
- CLOB API: real-time bid/ask, orderbook depth, midpoint

**Filters** (configurable):
- `min_volume_24h: 5000`
- `min_time_to_resolution: 1h`
- `max_spread: 0.10`
- `categories: ["crypto"]`

**Output**: `MarketOpportunity` dataclass.

```python
@dataclass
class MarketOpportunity:
    market_id: str
    question: str
    category: str
    yes_price: float
    no_price: float
    volume_24h: float
    spread: float
    end_date: datetime
    is_existing_position: bool
```

### 5.3 Claude Brain (Phase 2)

**Pipeline**: MarketOpportunity → Claude Scorer → EV Calculator → Kelly Sizer → Bayesian Updater → TradeSignal

#### Claude Probability Scorer
- Sends market question + context to Claude API (without showing market price to prevent anchoring)
- Uses tool use for structured JSON output: `{probability, confidence, reasoning}`
- Model: `claude-sonnet-4-20250514` (cost-effective, fast)
- Cool-down: don't re-evaluate same market within 15 minutes unless price moves >5%

#### EV Calculator
- `ev = p_true - market_price - effective_fee`
- Skip if `ev < 0.05` (5% minimum edge)
- Fee schedule: crypto 1.80%, sports 0.75%, politics 1.00%

#### Kelly Sizer
- `full_kelly = (p_true - market_price) / (1 - market_price)`
- Apply quarter Kelly: `position = full_kelly * 0.25 * available_bankroll`
- Scale by confidence: high=1.0, medium=0.6, low=0.3
- Hard caps: max 5% of bankroll, min $10, 30% cash reserve

#### Bayesian Updater (existing positions only)
- Uses log-odds form: `log_posterior = log_prior + log(likelihood_ratio)`
- Triggers: EV still positive → HOLD, EV flipped negative → EXIT, EV grew → ADD

**Output**: `TradeSignal` dataclass.

```python
@dataclass
class TradeSignal:
    market_id: str
    action: str                # "BUY_YES", "BUY_NO", "SELL", "HOLD"
    p_true: float
    p_market: float
    ev: float
    position_size_usd: float
    kelly_fraction: float
    confidence: str
    reasoning: str
```

### 5.4 Execution Engine

**Two implementations, one interface**:

```python
class BaseExecutor(ABC):
    async def place_order(self, signal) -> OrderResult
    async def cancel_order(self, order_id) -> bool
    async def get_positions(self) -> list[Position]
    async def get_balance(self) -> float
    async def redeem_resolved(self, market_id) -> float
```

#### Paper Executor
- Simulates fills at midpoint + configurable slippage (default 0.5%)
- Simulates taker fees by category
- Tracks virtual bankroll, positions, PnL in SQLite
- Resolves positions by checking actual market outcomes on Polymarket

#### Live Executor
- Uses `py-clob-client` for Polymarket CLOB
- Prefers GTC limit orders (maker = free + rebates) for Claude Brain signals
- Uses FOK orders (fill-or-kill) for copy trading signals (speed matters)
- Monitors order fill status
- Safety rails: daily loss limit, max concurrent positions, emergency stop

### 5.5 State (SQLite Database)

**Tables**:

```sql
-- Every trade (paper or live), full signal context
CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    timestamp DATETIME,
    market_id TEXT,
    question TEXT,
    category TEXT,
    side TEXT,
    price REAL,
    size_usd REAL,
    fee REAL,
    signal_source TEXT,        -- "copy" or "brain"
    signal_data JSON,          -- full signal as JSON for debugging
    order_type TEXT,           -- "GTC", "FOK"
    status TEXT,               -- "filled", "cancelled", "pending"
    leader_address TEXT,       -- null if from brain
    FOREIGN KEY (market_id) REFERENCES markets(id)
);

-- Current open positions
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    side TEXT,
    entry_price REAL,
    size_shares REAL,
    size_usd REAL,
    current_price REAL,
    unrealized_pnl REAL,
    signal_source TEXT,
    leader_address TEXT,
    opened_at DATETIME,
    UNIQUE(market_id, side)
);

-- Portfolio snapshots over time
CREATE TABLE portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME,
    total_value REAL,
    cash_balance REAL,
    positions_value REAL,
    daily_pnl REAL,
    total_pnl REAL
);

-- Cached Claude evaluations (for cool-down logic)
CREATE TABLE market_cache (
    market_id TEXT PRIMARY KEY,
    p_true REAL,
    confidence TEXT,
    reasoning TEXT,
    evaluated_at DATETIME
);

-- Tracked wallet profiles
CREATE TABLE wallet_profiles (
    address TEXT PRIMARY KEY,
    username TEXT,
    category_stats JSON,
    total_pnl REAL,
    total_volume REAL,
    is_suspicious BOOLEAN,
    last_scored DATETIME
);

-- Known trade IDs to prevent duplicate processing
CREATE TABLE known_trades (
    trade_id TEXT PRIMARY KEY,
    wallet_address TEXT,
    detected_at DATETIME
);
```

### 5.6 Telegram Bot

**Alerts** (bot → user):

| Alert | Trigger |
|-------|---------|
| Trade opened | New position from any signal source |
| Trade closed | Position exited or resolved |
| Market resolved | Win/loss outcome |
| Daily summary | Configurable time, once per day |
| Risk alert | Daily loss limit approaching |
| Bot paused | Loss limit hit or critical error |
| Leader change | New wallet added or removed from watch list |

**Commands** (user → bot):

| Command | Action |
|---------|--------|
| `/status` | Mode, bankroll, positions count, today's PnL |
| `/positions` | All open positions with current value |
| `/history` | Last 10 trades with outcomes |
| `/pnl` | Today, week, all-time P&L |
| `/leaders` | Currently tracked wallets with their stats |
| `/pause` | Stop trading, keep monitoring |
| `/resume` | Resume trading |
| `/mode paper/live` | Switch mode (with graduation check) |
| `/config` | Show current settings |
| `/kill` | Emergency stop — cancel all orders, stop bot |

**Security**: Only responds to configured `telegram_user_id`.

### 5.7 Web Dashboard (FastAPI)

**Tech**: FastAPI + Jinja2 templates + Chart.js via CDN + Tailwind CSS via CDN. No build step.

**Pages**:

| Page | Content |
|------|---------|
| Overview | Bankroll, PnL, positions count, bot status, mode, graduation progress |
| Positions | Table of open positions — source (copy/brain), market, side, entry, current, PnL |
| Trade Log | Paginated history with filters by source, category, outcome |
| PnL Chart | Portfolio value over time, daily returns bars |
| Leaders | Tracked wallets table — address, categories, WR, PF, last trade |
| Settings | View config, switch mode (with confirmation) |

**Access**: `localhost:8080`, optional password for VPS deployment.

### 5.8 Paper Trading & Graduation

**Graduation criteria before live trading**:

| Criteria | Threshold |
|----------|-----------|
| Paper trades completed | 200+ |
| Paper trading duration | 14+ days |
| Win rate | >52% |
| Profit factor | >1.2 |
| Max drawdown | <15% |
| No bugs for 48h | Zero errors |

**Go-live sequence**:
1. All criteria green on dashboard
2. User sends `/mode live` via Telegram
3. Bot asks confirmation
4. First 48 hours: 10% of configured bankroll (auto-scaling)
5. After 48h with no issues: full bankroll

## 6. Risk Management

**Shared across both strategies**:

| Control | Rule |
|---------|------|
| Position sizing | Quarter Kelly, max 5% of bankroll |
| Cash reserve | Always keep 30% in cash |
| Concurrent positions | Max 10 total |
| Daily loss limit | Pause at -5% daily |
| Per-market limit | Max one position per market |
| Emergency stop | `/kill` cancels all orders immediately |

## 7. External Dependencies

```
anthropic>=0.45.0        # Claude API (Phase 2)
py-clob-client>=0.18.0   # Polymarket official SDK
python-telegram-bot>=21.0 # Telegram integration
fastapi>=0.115.0         # Web dashboard
uvicorn>=0.32.0          # ASGI server
jinja2>=3.1.0            # HTML templating
pyyaml>=6.0              # Config parsing
aiosqlite>=0.20.0        # Async SQLite
httpx>=0.27.0            # Async HTTP client
```

## 8. Configuration

All settings live in `config.yaml` (see `config.example.yaml` for full reference with comments).

Key principle: **config changes should never require code changes.** Every threshold, interval, and limit is configurable.

## 9. Project Structure

```
copywallet/
├── CLAUDE.md
├── config.example.yaml
├── config.yaml              # (gitignored)
├── .env                     # (gitignored)
├── .gitignore
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── scanner.py
│   │   └── models.py
│   ├── brain/
│   │   ├── __init__.py
│   │   ├── claude_scorer.py
│   │   ├── ev_calculator.py
│   │   ├── kelly_sizer.py
│   │   ├── bayesian.py
│   │   └── models.py
│   ├── copytrade/
│   │   ├── __init__.py
│   │   ├── discovery.py
│   │   ├── monitor.py
│   │   ├── filters.py
│   │   └── models.py
│   ├── executor/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── paper.py
│   │   └── live.py
│   ├── state/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   └── models.py
│   ├── monitor/
│   │   ├── __init__.py
│   │   ├── telegram_bot.py
│   │   └── dashboard.py
│   └── templates/
│       ├── base.html
│       ├── overview.html
│       ├── positions.html
│       ├── trades.html
│       ├── pnl.html
│       ├── leaders.html
│       └── settings.html
├── static/
│   └── chart_config.js
├── tests/
│   ├── test_filters.py
│   ├── test_kelly_sizer.py
│   ├── test_ev_calculator.py
│   ├── test_bayesian.py
│   ├── test_paper_executor.py
│   ├── test_discovery.py
│   └── test_monitor.py
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-07-copywallet-design.md
```

## 10. Security Considerations

1. **Private keys**: Only in `.env` / `config.yaml`, both gitignored. Only the Executor module reads them.
2. **Dedicated wallet**: Never use a main wallet. Fund with limited USDC.e on Polygon.
3. **Dependency audit**: Pin all versions in requirements.txt. Review before installing.
4. **No third-party copy trading code**: All monitoring and filtering is custom-built.
5. **Monitoring is read-only**: Uses only public, unauthenticated Polymarket APIs.
6. **Telegram auth**: Bot only responds to configured user ID.
7. **Dashboard**: Local access only by default. Password-protected on VPS.

## 11. Success Criteria

**Phase 1 (Copy Trading)**:
- Bot discovers and scores top wallets automatically
- Trade detection latency <5 seconds from leader's trade
- All 7 filters working correctly (verified in paper mode)
- Telegram alerts and commands functional
- Dashboard showing positions, trades, PnL, leaders
- Paper trading profitable over 2+ weeks with 200+ trades

**Phase 2 (Claude Brain)**:
- Claude probability estimates have Brier score <0.25
- EV filter eliminates >80% of markets (correct behavior)
- Kelly sizing stays within risk limits
- Both strategies produce independent, non-conflicting signals

**Phase 3 (Live)**:
- All graduation criteria met
- Live trading performance within 80% of paper trading results
- No security incidents
- Bot runs 24/7 on VPS with <0.1% downtime
