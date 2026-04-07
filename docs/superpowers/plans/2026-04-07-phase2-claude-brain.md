# Phase 2: Claude Brain Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude API as a second signal source — estimate true probabilities for Polymarket markets, calculate expected value, size with Kelly, and update existing positions with Bayesian logic.

**Architecture:** The Brain module takes MarketOpportunity objects from the Scanner, sends them to Claude for probability estimation (without showing the market price to prevent anchoring), calculates EV, filters by threshold, sizes with Kelly, and outputs TradeSignal objects to the shared Executor. For existing positions, Bayesian updating decides HOLD/EXIT/ADD.

**Tech Stack:** anthropic SDK, existing infrastructure (config, DB, executor, rate limiter)

**Depends on:** Phase 1 complete (config, database, executor, main loop all working)

---

## File Structure (new/modified files only)

```
src/
├── scanner/
│   ├── scanner.py          # NEW: Fetch and filter active markets
│   └── models.py           # NEW: MarketOpportunity dataclass
├── brain/
│   ├── claude_scorer.py    # NEW: Claude API probability estimation
│   ├── ev_calculator.py    # NEW: Expected value with fee adjustment
│   ├── bayesian.py         # NEW: Log-odds Bayesian updater
│   ├── pipeline.py         # NEW: Brain pipeline orchestrator
│   └── models.py           # NEW: TradeSignal dataclass
├── main.py                 # MODIFY: Wire brain into main loop
└── config.py               # Already has claude_brain config (disabled by default)

tests/
├── test_ev_calculator.py   # NEW
├── test_bayesian.py        # NEW
├── test_scanner.py         # NEW
└── test_brain_integration.py # NEW
```

---

## Task P2-1: Market Scanner + Brain Models

**Files:**
- Create: `src/scanner/models.py`
- Create: `src/scanner/scanner.py`
- Create: `src/brain/models.py`
- Create: `tests/test_scanner.py`

### `src/scanner/models.py`

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class MarketOpportunity:
    market_id: str          # condition_id
    token_id_yes: str
    token_id_no: str
    question: str
    category: str
    yes_price: float        # current YES midpoint
    no_price: float         # current NO midpoint
    volume_24h: float
    spread: float
    end_date: str
    neg_risk: bool
    is_existing_position: bool
```

### `src/brain/models.py`

```python
from dataclasses import dataclass

@dataclass
class TradeSignal:
    market_id: str
    token_id: str
    question: str
    category: str
    action: str             # "BUY_YES", "BUY_NO", "SELL", "HOLD"
    p_true: float           # Claude's probability estimate
    p_market: float         # current market price
    ev: float               # expected value per dollar
    position_size_usd: float
    kelly_fraction: float
    confidence: str         # "high", "medium", "low"
    reasoning: str
    neg_risk: bool
```

### `src/scanner/scanner.py`

```python
class MarketScanner:
    def __init__(self, api: PolymarketAPI, db: Database, config: ScannerConfig):
        ...
    
    async def scan(self) -> list[MarketOpportunity]:
        """
        1. Fetch active events from Gamma API by category
        2. For each market in events:
           - Get token IDs for YES/NO outcomes
           - Get midpoint prices from CLOB
           - Get spread from CLOB
           - Filter: min_volume_24h, min_time_to_resolution, max_spread
           - Check if we have existing position
        3. Return list of MarketOpportunity
        """
```

### Tests: `tests/test_scanner.py`

- Test MarketOpportunity creation
- Test filter logic (volume, spread, time to resolution)

- [ ] Write failing tests
- [ ] Implement models + scanner
- [ ] Verify tests pass
- [ ] Commit: `feat: add market scanner and brain models`

---

## Task P2-2: EV Calculator

**Files:**
- Create: `src/brain/ev_calculator.py`
- Create: `tests/test_ev_calculator.py`

### `src/brain/ev_calculator.py`

```python
def calculate_ev(p_true: float, market_price: float, side: str, fee_rate: float) -> float:
    """Calculate expected value per dollar for a prediction market trade.
    
    For BUY_YES: ev = p_true - market_price - effective_fee
    For BUY_NO:  ev = (1 - p_true) - (1 - market_price) - effective_fee
    
    effective_fee = fee_rate * market_price * (1 - market_price)
    """

def determine_side(p_true: float, market_price: float) -> str:
    """Determine whether to buy YES or NO based on where the edge is.
    
    If p_true > market_price → BUY_YES (we think YES is underpriced)
    If p_true < market_price → BUY_NO (we think NO is underpriced)
    If equal → HOLD
    """

def should_trade(ev: float, min_ev_threshold: float = 0.05) -> bool:
    """Only trade if EV exceeds minimum threshold (default 5%)."""
```

### Tests: 8 tests

- [ ] `test_ev_buy_yes_positive_edge` — p=0.65, market=0.48, fee=0.018 → positive EV
- [ ] `test_ev_buy_no_positive_edge` — p=0.30, market=0.55, fee=0.018 → positive EV on NO side
- [ ] `test_ev_no_edge` — p=0.50, market=0.50 → EV ≈ 0 (minus fee)
- [ ] `test_ev_fee_reduces_edge` — same p/market, higher fee → lower EV
- [ ] `test_determine_side_buy_yes` — p > market → BUY_YES
- [ ] `test_determine_side_buy_no` — p < market → BUY_NO
- [ ] `test_should_trade_above_threshold` — ev=0.08, threshold=0.05 → True
- [ ] `test_should_trade_below_threshold` — ev=0.03, threshold=0.05 → False

- [ ] Write failing tests
- [ ] Implement ev_calculator.py
- [ ] Verify tests pass
- [ ] Commit: `feat: add EV calculator with fee-adjusted edge and side determination`

---

## Task P2-3: Bayesian Updater

**Files:**
- Create: `src/brain/bayesian.py`
- Create: `tests/test_bayesian.py`

### `src/brain/bayesian.py`

```python
import math

def to_log_odds(p: float) -> float:
    """Convert probability to log-odds. Clamp p to (0.01, 0.99) to avoid infinity."""
    p = max(0.01, min(0.99, p))
    return math.log(p / (1 - p))

def from_log_odds(lo: float) -> float:
    """Convert log-odds back to probability."""
    return math.exp(lo) / (1 + math.exp(lo))

def bayesian_update(prior: float, likelihood_true: float, likelihood_false: float) -> float:
    """Full Bayes update: P(H|E) = P(E|H)*P(H) / P(E)
    
    prior: P(H) — current probability estimate
    likelihood_true: P(E|H) — probability of evidence if hypothesis is true
    likelihood_false: P(E|not H) — probability of evidence if hypothesis is false
    """
    numerator = likelihood_true * prior
    denominator = numerator + likelihood_false * (1 - prior)
    if denominator == 0:
        return prior
    return numerator / denominator

def log_odds_update(prior: float, likelihood_ratio: float) -> float:
    """Sequential update using log-odds form.
    
    log_posterior = log_prior + log(likelihood_ratio)
    
    likelihood_ratio = P(E|H) / P(E|not H)
    """
    lo = to_log_odds(prior)
    lr = math.log(max(likelihood_ratio, 0.01))
    return from_log_odds(lo + lr)

def decide_position_action(prior_p: float, new_p: float, market_price: float, 
                           min_ev: float = 0.05) -> str:
    """Decide what to do with an existing position based on updated probability.
    
    Returns: "HOLD", "EXIT", or "ADD"
    
    - If new EV is still positive and above threshold → HOLD
    - If new EV flipped negative → EXIT
    - If new EV grew significantly (>2x prior EV) → ADD
    """
```

### Tests: 8 tests

- [ ] `test_to_log_odds_50_percent` — p=0.50 → log_odds=0
- [ ] `test_roundtrip_log_odds` — to_log_odds → from_log_odds = original
- [ ] `test_bayesian_update_strong_evidence` — prior=0.35, strong evidence → posterior > 0.60
- [ ] `test_bayesian_update_weak_evidence` — prior=0.35, weak evidence → posterior ≈ 0.40
- [ ] `test_log_odds_update` — same result as bayesian_update
- [ ] `test_decide_action_hold` — EV still positive → HOLD
- [ ] `test_decide_action_exit` — EV flipped negative → EXIT
- [ ] `test_decide_action_add` — EV doubled → ADD

- [ ] Write failing tests
- [ ] Implement bayesian.py
- [ ] Verify tests pass
- [ ] Commit: `feat: add Bayesian updater with log-odds sequential updates`

---

## Task P2-4: Claude Scorer

**Files:**
- Create: `src/brain/claude_scorer.py`

No unit tests — wraps external API. Tested in integration test.

### `src/brain/claude_scorer.py`

```python
import anthropic
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class ClaudeScorer:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        """If api_key is empty, all methods return None (allows running without Claude)."""
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self._model = model
    
    async def estimate_probability(self, question: str, category: str, 
                                    context: str = "") -> dict | None:
        """Ask Claude to estimate true probability of a market outcome.
        
        CRITICAL: Do NOT include the current market price in the prompt.
        This prevents anchoring bias.
        
        Returns: {"probability": float, "confidence": str, "reasoning": str}
        or None if API key not configured or call fails.
        
        Uses tool_use for structured output:
        - Tool name: "probability_estimate"
        - Schema: probability (float 0-1), confidence (low/medium/high), reasoning (string)
        
        Prompt design:
        - "You are a calibrated prediction market analyst."
        - "Estimate the TRUE probability (0.00-1.00)."
        - "Consider base rates. Penalize extreme confidence."
        - "If you say 70%, ~7 out of 10 such calls should resolve YES."
        - Include category context (crypto needs different framing than politics)
        """
    
    def _build_prompt(self, question: str, category: str, context: str) -> str:
        """Build the estimation prompt WITHOUT market price."""
```

- [ ] Implement claude_scorer.py
- [ ] Commit: `feat: add Claude probability scorer with anti-anchoring prompt design`

---

## Task P2-5: Brain Pipeline

**Files:**
- Create: `src/brain/pipeline.py`
- Create: `tests/test_brain_integration.py`

### `src/brain/pipeline.py`

```python
class BrainPipeline:
    def __init__(self, scorer: ClaudeScorer, db: Database, config: Config):
        self._scorer = scorer
        self._db = db
        self._config = config
    
    async def evaluate(self, market: MarketOpportunity) -> TradeSignal | None:
        """Full brain pipeline for a single market:
        
        1. Check cooldown — skip if evaluated within cooldown_minutes
        2. Call Claude scorer for p_true
        3. Determine side (BUY_YES or BUY_NO)
        4. Calculate EV with fee adjustment
        5. Check EV threshold — skip if too low
        6. Size with Kelly
        7. Cache evaluation in DB
        8. Return TradeSignal
        """
    
    async def update_position(self, market: MarketOpportunity, 
                               position: dict) -> TradeSignal | None:
        """Bayesian update for existing positions:
        
        1. Get prior p_true from market_cache
        2. Get new Claude estimate
        3. Update with Bayesian logic
        4. Decide action (HOLD/EXIT/ADD)
        5. If EXIT → return sell signal
        6. If ADD → return buy signal with incremental size
        7. If HOLD → return None
        """
```

### Tests: `tests/test_brain_integration.py`

Mock ClaudeScorer to return fixed probabilities. Test:
- Full pipeline produces a TradeSignal when EV is positive
- Pipeline returns None when EV is below threshold
- Pipeline respects cooldown
- Bayesian update produces EXIT when probability flips

- [ ] Write failing tests
- [ ] Implement pipeline.py
- [ ] Verify tests pass
- [ ] Commit: `feat: add brain pipeline orchestrating scorer, EV, Kelly, and Bayesian updates`

---

## Task P2-6: Wire Brain into Main Loop

**Files:**
- Modify: `src/main.py`
- Modify: `requirements.txt` (add `anthropic>=0.45.0`)

### Changes to main.py:

```python
# Add imports
from src.scanner.scanner import MarketScanner
from src.brain.claude_scorer import ClaudeScorer
from src.brain.pipeline import BrainPipeline

# In main():
# After existing component init, add:
scanner = MarketScanner(api, db, config.scanner)
claude_scorer = ClaudeScorer(config.anthropic_api_key, config.claude_brain.model)
brain = BrainPipeline(claude_scorer, db, config)

# In the main loop, after copy trading block:
if config.claude_brain.enabled:
    try:
        markets = await scanner.scan()
        for market in markets:
            signal = await brain.evaluate(market)
            if signal and signal.action != "HOLD":
                result = await executor.execute(signal)
                if result.status == "filled":
                    await telegram.send_trade_alert(signal, result)
    except Exception as e:
        logger.error(f"Brain error: {e}")
```

Note: The executor's `execute()` method currently accepts `CopySignal`. We need to make it accept `TradeSignal` too. The simplest approach: create a `signal_to_copy_signal()` adapter function, OR modify the executor to accept a protocol/union type. The adapter is simpler and less invasive.

- [ ] Add anthropic to requirements.txt
- [ ] Modify main.py to wire in brain
- [ ] Add signal adapter for executor compatibility
- [ ] Run full test suite
- [ ] Commit: `feat: wire Claude brain into main loop as second signal source`

---

## Summary

| Task | Files | Tests | Delivers |
|------|-------|-------|----------|
| P2-1 | scanner/models+scanner, brain/models | 4+ | Market scanning + data models |
| P2-2 | brain/ev_calculator | 8 | EV calculation with fees |
| P2-3 | brain/bayesian | 8 | Bayesian updating for position management |
| P2-4 | brain/claude_scorer | 0 (I/O) | Claude API integration |
| P2-5 | brain/pipeline | 4+ | Full brain orchestration |
| P2-6 | main.py, requirements.txt | 0 | Wire everything together |

**Total: 6 tasks, ~24 new tests, ~600 lines of new code.**
