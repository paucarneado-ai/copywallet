# Copywallet — Autonomous Polymarket Trading Bot

## Project Overview

Copywallet is a fully autonomous trading bot for Polymarket prediction markets. It uses two signal strategies:
- **Phase 1 (current):** Smart Copy Trading — monitor top wallets, filter by category dominance, mirror their trades with Kelly sizing
- **Phase 2 (future):** Claude Brain — Claude API estimates true probabilities, EV filter, Bayesian updating, Kelly sizing

Built in Python. Paper trading first, then live with $2-10K.

## Architecture

```
src/
├── main.py              # Entry point, main loop
├── config.py            # Loads config.yaml, validates settings
├── scanner/             # Fetches markets and prices from Polymarket APIs
├── brain/               # Claude probability scorer + EV + Kelly + Bayes (Phase 2)
├── copytrade/           # Wallet discovery, monitoring, filtering (Phase 1)
├── executor/            # Paper and Live execution via py-clob-client
├── state/               # SQLite database, positions, trades, PnL
├── monitor/             # Telegram bot + FastAPI web dashboard
└── templates/           # Jinja2 HTML templates for dashboard
```

## Tech Stack

- **Python 3.11+**
- **py-clob-client** — official Polymarket SDK for order execution
- **anthropic** — Claude API for probability estimation (Phase 2)
- **python-telegram-bot** — Telegram alerts and commands
- **FastAPI + Jinja2 + Chart.js** — web dashboard
- **SQLite via aiosqlite** — local database
- **httpx** — async HTTP client for API calls
- **pyyaml** — configuration

## Polymarket APIs Used

| API | Base URL | Auth | Purpose |
|-----|----------|------|---------|
| Gamma | `https://gamma-api.polymarket.com` | No | Market metadata, categories, tags |
| Data | `https://data-api.polymarket.com` | No | Trades, positions, leaderboard, activity |
| CLOB | `https://clob.polymarket.com` | Read: No, Write: Yes | Prices, orderbook, order placement |
| WebSocket | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | No | Real-time price updates |

## Critical Rules

### Security
- **NEVER** commit `.env`, `config.yaml`, or any file containing API keys or private keys
- **NEVER** expose private keys to monitoring/read-only code — only the Executor module needs keys
- **ALWAYS** use a dedicated Polymarket wallet with limited funds, never a main wallet
- **ALWAYS** audit dependencies before installing — the copy-trading-bot category on GitHub is plagued with malware

### Trading Safety
- Paper mode and live mode use the SAME logic — only the Executor swaps
- The bot REFUSES to switch to live mode unless graduation criteria are met (200+ trades, 2+ weeks, WR >52%)
- Quarter Kelly sizing (25% of full Kelly) — never full Kelly. For copy signals use 0.15-0.20 (win rate as probability is an approximation)
- Hard caps: max 5% of bankroll per position, 30% cash reserve, max 10 concurrent positions
- Daily loss limit: if portfolio drops 5% in a day, PAUSE trading and alert via Telegram

### Error Handling
- Exponential backoff on all API calls (1s, 2s, 4s, 8s, max 60s)
- Circuit breaker per endpoint: 5 consecutive failures → pause 2 min → alert via Telegram
- Idempotent order placement: track order IDs in DB before submission
- SQLite WAL mode (`PRAGMA journal_mode=WAL`) for concurrent access
- Graceful shutdown on SIGTERM/SIGINT: cancel pending orders, save state
- Startup reconciliation: check CLOB for pending orders, sync positions

### Rate Limiting
- Polymarket limits: 100 reads/min (Data/Gamma), 60 orders/min (CLOB)
- Copy trading poller: round-robin one wallet per cycle, NOT all wallets every cycle
- Max 15 tracked wallets (each checked every ~30s with round-robin)
- Add 50ms delay between paginated calls
- RateLimiter class tracks requests/min and delays when approaching limit

### Market ID Disambiguation
- `condition_id` = market-level identifier (from Gamma API `conditionId`)
- `token_id` = specific outcome token (YES or NO) for CLOB operations
- `market_id` in our models = `condition_id`
- All CLOB operations (price, book, orders) require `token_id`
- Data models must carry BOTH `market_id` and `token_id`
- Check `negRisk` flag to route to correct exchange contract

### Code Style
- Type hints on all function signatures
- Dataclasses for all data models (MarketOpportunity, TradeSignal, CopySignal, WalletProfile, etc.)
- Async where possible (httpx, aiosqlite) — the main loop is async
- Every module has a clear single responsibility
- Config-driven: all thresholds, intervals, and limits come from config.yaml
- Logging via Python `logging` module — structured, leveled, no print statements

### Polymarket-Specific
- Users trade through **proxy wallets** (Gnosis Safe), not EOA — always use proxy address
- Data API returns max 100 results — always paginate with offset
- Rate limits: 100 reads/min (Data/Gamma), 60 orders/min (CLOB)
- USDC.e on Polygon (6 decimals), not native USDC
- Check market's `negRisk` flag to route to correct exchange contract
- Maker orders (GTC limit) are free + earn rebates. Prefer over taker (FOK) when latency allows

### Copy Trading Specific
- Only copy trades in categories where the wallet has >55% win rate AND >20 resolved positions
- Reject trades older than 60 seconds (staleness filter)
- Reject if price drifted >5% since leader's entry
- Reject if bid-ask spread >$0.10
- Reject if estimated slippage >2%
- Never copy a leader who is REDUCING their position
- Use leader's category win rate as probability input for Kelly sizing — not their position size
- Monitor leader exits — when they sell, we sell too

## Testing

- Tests live in `tests/`
- Run with: `python -m pytest tests/ -v`
- Test the math modules (EV, Kelly, Bayes) with known inputs/outputs
- Test the paper executor with simulated market data
- Test copy trading filters with mock trade data

## Development Workflow

- Feature branches for all work
- Incremental commits with descriptive messages
- Paper trading validation before any live trading code
- Config changes should never require code changes
