# Copywallet Design — Review Fixes

**Date**: 2026-04-07
**Applies to**: `2026-04-07-copywallet-design.md`

These fixes address issues found during spec review. All are incorporated into the implementation plan.

---

## Critical Fixes

### C1: USDC.e → Native USDC Transition

Polymarket is transitioning from bridged USDC.e to native USDC on Polygon (Circle partnership, Feb 2026). The bot must not hardcode the token contract.

**Fix**: Add `usdc_contract_address` to config.yaml. On startup, verify the configured address matches what Polymarket's exchange contracts expect by querying the contract's `getCollateral()` function. Default to USDC.e (`0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`) but make it swappable.

### C2: Rate Limiting Strategy

Polling 10 wallets at 2s intervals = 300 req/min, exceeding the 100 reads/min limit.

**Fix**: Implement a **staggered batch poller**:
- One poll cycle = 2 seconds
- Each cycle polls ONE wallet (round-robin across tracked wallets)
- With 10 wallets: each wallet is checked every 20 seconds
- Total: 30 req/min for polling + headroom for enrichment calls
- Add a `RateLimiter` class that tracks requests per minute and delays when approaching the limit
- Cap tracked wallets at 15 maximum (configurable)

### C3: No `/estimate-fill` Endpoint

The CLOB API has no `/estimate-fill` endpoint. Slippage must be calculated manually.

**Fix**: Implement `estimate_fill_price(token_id, side, size)`:
```python
async def estimate_fill_price(token_id: str, side: str, size: float) -> float:
    book = await clob_client.get_book(token_id)
    levels = book['asks'] if side == 'BUY' else book['bids']
    remaining = size
    total_cost = 0.0
    for level in levels:
        fill_at_level = min(remaining, level['size'])
        total_cost += fill_at_level * level['price']
        remaining -= fill_at_level
        if remaining <= 0:
            break
    if remaining > 0:
        return None  # insufficient liquidity
    return total_cost / size  # weighted average fill price
```

### C4: Error Handling Strategy

**Fix**: Add these patterns throughout:

| Pattern | Where | Behavior |
|---------|-------|----------|
| **Exponential backoff** | All API calls | Wait 1s, 2s, 4s, 8s, max 60s on consecutive failures |
| **Circuit breaker** | Per API endpoint | After 5 consecutive failures, pause that endpoint for 2 minutes, alert via Telegram |
| **Idempotent orders** | Executor | Track order IDs in DB before submission; on retry, check if order already exists |
| **WAL mode** | SQLite | `PRAGMA journal_mode=WAL` on startup for concurrent read/write |
| **Graceful shutdown** | Main loop | Handle SIGTERM/SIGINT: cancel pending orders, save state, exit cleanly |
| **Startup reconciliation** | Boot sequence | On start, check CLOB for pending orders, sync positions with on-chain state |

### C5: Kelly with Win Rate as Probability — Known Limitation

Using a leader's category win rate (e.g., 62%) as P_true for every trade in that category is an approximation. It over-sizes low-edge trades and under-sizes high-edge trades.

**Fix**: 
- Phase 1: Accept this limitation. Document it. Apply a conservative Kelly fraction (0.15-0.20 instead of 0.25) for copy signals to compensate for the imprecise probability estimate.
- Phase 3: When both strategies run, use Claude Brain's per-market P_true for trades where both signals agree. This replaces the aggregate win rate with a market-specific estimate.

---

## Important Fixes

### I1: negRisk Market Routing

**Fix**: Add `neg_risk: bool` and `token_id: str` to all data models (`MarketOpportunity`, `CopySignal`, `TradeSignal`). The executor checks `neg_risk` and routes to the correct exchange contract:
- Standard: `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`
- NegRisk: `0xC5d563A36AE78145C45a50134d48A1215220f80a`

### I3: Database Unique Constraint

**Fix**: Change positions table constraint to `UNIQUE(market_id, side, signal_source)`. This allows both copy and brain to hold positions in the same market independently. Risk limits check total exposure per market across both sources.

### I5: EV Formula for BUY_NO

**Fix**: Generalize EV calculation:
```python
def calculate_ev(p_true: float, market_price: float, side: str, fee: float) -> float:
    if side == "BUY_YES":
        return p_true - market_price - fee
    else:  # BUY_NO
        return (1 - p_true) - (1 - market_price) - fee
```

### I6: Dashboard Auth Enforcement

**Fix**: On startup, if `dashboard.host != "127.0.0.1"` and `dashboard.password` is empty, refuse to start and log: "Dashboard password required when binding to non-localhost address."

### I7: Fee Schedule Configurable

**Fix**: Move fee rates to config.yaml:
```yaml
fees:
  crypto: 0.018
  sports: 0.0075
  politics: 0.01
  economics: 0.015
  geopolitics: 0.0
  default: 0.01
```

### I8: Logging Strategy

**Fix**: Structured logging with levels:
- **DEBUG**: All API requests/responses with timing
- **INFO**: Signals generated, trades executed, leader changes, portfolio snapshots
- **WARNING**: Filter rejections (with reason), rate limit approaching, degraded performance
- **ERROR**: Failed orders, API errors, DB errors
- **CRITICAL**: Loss limit hit, unrecoverable errors, security alerts

Format: `[TIMESTAMP] [LEVEL] [MODULE] message | key=value pairs`

### Additional: Token Approval Check

The live executor must verify USDC allowance on both exchange contracts at startup. If insufficient:
```python
async def check_approvals(self):
    allowance = await self.get_usdc_allowance(CTF_EXCHANGE)
    if allowance < MIN_ALLOWANCE:
        logger.error("Insufficient USDC allowance on CTF Exchange. Run approval transaction first.")
        raise SetupError("Token approval required")
```

### Additional: Market ID Clarification

Throughout the codebase:
- `condition_id` = the market's condition identifier (from Gamma API `conditionId`)
- `token_id` = the specific outcome token (YES or NO) identifier (needed for CLOB operations)
- `market_id` in our models = `condition_id` (the market-level identifier)
- All CLOB operations require `token_id`, looked up via Gamma API

Data models carry both: `market_id: str` (condition) and `token_id: str` (outcome token).
