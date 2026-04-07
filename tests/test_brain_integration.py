"""Integration tests for the brain pipeline orchestrating scorer, EV, Kelly, and Bayesian updates.

Exercises the full BrainPipeline end-to-end with a mocked scorer,
real in-memory SQLite, and real EV/Kelly/Bayesian functions.
"""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock

from src.brain.pipeline import BrainPipeline, CONFIDENCE_MULTIPLIERS
from src.scanner.models import MarketOpportunity
from src.state.database import Database
from src.config import Config, RiskConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> Config:
    """Build a test Config with sensible risk defaults."""
    risk_kwargs = dict(
        starting_bankroll=10000,
        kelly_fraction=0.25,
        max_position_pct=0.05,
        cash_reserve_pct=0.30,
        min_trade_size=10,
    )
    risk_kwargs.update(overrides.pop("risk_overrides", {}))
    defaults = dict(
        mode="paper",
        risk=RiskConfig(**risk_kwargs),
        fees={"crypto": 0.018, "default": 0.01},
    )
    defaults.update(overrides)
    return Config(**defaults)


def _make_market(**overrides) -> MarketOpportunity:
    """Build a MarketOpportunity with defaults for a BTC market."""
    defaults = dict(
        market_id="cond_btc",
        token_id_yes="tok_yes",
        token_id_no="tok_no",
        question="Will BTC be above $100K?",
        category="crypto",
        yes_price=0.48,
        no_price=0.52,
        volume_24h=50000,
        spread=0.03,
        end_date="2099-01-01T00:00:00Z",
        neg_risk=False,
        is_existing_position=False,
    )
    defaults.update(overrides)
    return MarketOpportunity(**defaults)


def _make_scorer(
    probability: float = 0.65,
    confidence: str = "high",
    reasoning: str = "Test reasoning",
) -> MagicMock:
    """Build a mock scorer returning a fixed probability estimate."""
    scorer = MagicMock()
    scorer.estimate_probability.return_value = {
        "probability": probability,
        "confidence": confidence,
        "reasoning": reasoning,
    }
    return scorer


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
# evaluate() — positive EV produces a TradeSignal
# ---------------------------------------------------------------------------


class TestEvaluateProducesSignal:
    """Pipeline should produce a BUY_YES signal when p_true >> market_price."""

    @pytest.mark.asyncio
    async def test_pipeline_produces_signal_on_positive_ev(self, db: Database) -> None:
        """p=0.65 vs market=0.48 with high confidence gives strong EV and position."""
        config = _make_config()
        scorer = _make_scorer(probability=0.65)
        pipeline = BrainPipeline(scorer, db, config)

        signal = await pipeline.evaluate(_make_market())

        assert signal is not None
        assert signal.action == "BUY_YES"
        assert signal.p_true == 0.65
        assert signal.ev > 0.05
        assert signal.position_size_usd > 0
        assert signal.market_id == "cond_btc"
        assert signal.token_id == "tok_yes"
        assert signal.confidence == "high"
        assert signal.neg_risk is False


# ---------------------------------------------------------------------------
# evaluate() — low EV returns None
# ---------------------------------------------------------------------------


class TestEvaluateReturnsNoneOnLowEv:
    """Pipeline returns None when the edge is too thin to trade."""

    @pytest.mark.asyncio
    async def test_pipeline_returns_none_on_low_ev(self, db: Database) -> None:
        """p=0.50 vs market=0.48 gives tiny EV below the 0.05 threshold."""
        config = _make_config()
        scorer = _make_scorer(probability=0.50)
        pipeline = BrainPipeline(scorer, db, config)

        signal = await pipeline.evaluate(_make_market())

        assert signal is None


# ---------------------------------------------------------------------------
# evaluate() — scorer failure returns None
# ---------------------------------------------------------------------------


class TestEvaluateReturnsNoneOnScorerFailure:
    """Pipeline returns None when the scorer cannot produce an estimate."""

    @pytest.mark.asyncio
    async def test_pipeline_returns_none_when_scorer_fails(self, db: Database) -> None:
        """Scorer returning None should cause pipeline to bail gracefully."""
        config = _make_config()
        scorer = MagicMock()
        scorer.estimate_probability.return_value = None

        pipeline = BrainPipeline(scorer, db, config)
        signal = await pipeline.evaluate(_make_market())

        assert signal is None


# ---------------------------------------------------------------------------
# evaluate() — cooldown prevents re-evaluation
# ---------------------------------------------------------------------------


class TestEvaluateRespectsCooldown:
    """Pipeline skips re-evaluation within the cooldown window."""

    @pytest.mark.asyncio
    async def test_pipeline_respects_cooldown(self, db: Database) -> None:
        """First call produces signal; second call within cooldown returns None."""
        config = _make_config()
        scorer = _make_scorer(probability=0.65)
        pipeline = BrainPipeline(scorer, db, config)
        market = _make_market()

        signal1 = await pipeline.evaluate(market)
        assert signal1 is not None

        signal2 = await pipeline.evaluate(market)
        assert signal2 is None

        # Scorer should only have been called once
        assert scorer.estimate_probability.call_count == 1


# ---------------------------------------------------------------------------
# evaluate() — BUY_NO side selection
# ---------------------------------------------------------------------------


class TestEvaluateBuyNoSide:
    """Pipeline produces BUY_NO when p_true is well below market_price."""

    @pytest.mark.asyncio
    async def test_pipeline_produces_buy_no_signal(self, db: Database) -> None:
        """p=0.30 vs market=0.55 -> BUY_NO with positive EV."""
        config = _make_config()
        scorer = _make_scorer(probability=0.30)
        market = _make_market(yes_price=0.55, no_price=0.45)
        pipeline = BrainPipeline(scorer, db, config)

        signal = await pipeline.evaluate(market)

        assert signal is not None
        assert signal.action == "BUY_NO"
        assert signal.token_id == "tok_no"
        assert signal.ev > 0.05


# ---------------------------------------------------------------------------
# evaluate() — confidence multiplier reduces position size
# ---------------------------------------------------------------------------


class TestConfidenceMultiplierEffect:
    """Lower confidence should yield smaller position sizes via Kelly adjustment."""

    @pytest.mark.asyncio
    async def test_low_confidence_reduces_position(self, db: Database) -> None:
        """Compare high-confidence vs low-confidence sizing for same edge."""
        config = _make_config()

        scorer_high = _make_scorer(probability=0.65, confidence="high")
        pipeline_high = BrainPipeline(scorer_high, db, config)
        signal_high = await pipeline_high.evaluate(_make_market(market_id="mkt_h"))

        scorer_low = _make_scorer(probability=0.65, confidence="low")
        pipeline_low = BrainPipeline(scorer_low, db, config)
        signal_low = await pipeline_low.evaluate(_make_market(market_id="mkt_l"))

        assert signal_high is not None
        assert signal_low is not None
        assert signal_high.position_size_usd > signal_low.position_size_usd


# ---------------------------------------------------------------------------
# evaluate() — HOLD side returns None
# ---------------------------------------------------------------------------


class TestEvaluateHoldSide:
    """Pipeline returns None when p_true equals market_price (HOLD)."""

    @pytest.mark.asyncio
    async def test_pipeline_returns_none_on_hold(self, db: Database) -> None:
        """p=0.48 vs market=0.48 -> HOLD -> no signal."""
        config = _make_config()
        scorer = _make_scorer(probability=0.48)
        pipeline = BrainPipeline(scorer, db, config)

        signal = await pipeline.evaluate(_make_market())

        assert signal is None


# ---------------------------------------------------------------------------
# update_position() — EXIT on flipped direction
# ---------------------------------------------------------------------------


class TestUpdatePositionExit:
    """update_position returns SELL signal when probability flips direction."""

    @pytest.mark.asyncio
    async def test_update_position_exit(self, db: Database) -> None:
        """Prior was bullish (0.60) but new estimate is bearish (0.40) -> EXIT/SELL."""
        config = _make_config()
        scorer = _make_scorer(probability=0.40)
        pipeline = BrainPipeline(scorer, db, config)
        market = _make_market()

        signal = await pipeline.update_position(market, prior_p=0.60)

        assert signal is not None
        assert signal.action == "SELL"


# ---------------------------------------------------------------------------
# update_position() — HOLD returns None
# ---------------------------------------------------------------------------


class TestUpdatePositionHold:
    """update_position returns None when position should be held."""

    @pytest.mark.asyncio
    async def test_update_position_hold(self, db: Database) -> None:
        """Prior 0.60, new 0.62 vs market 0.48 -> HOLD (edge steady) -> None."""
        config = _make_config()
        scorer = _make_scorer(probability=0.62)
        pipeline = BrainPipeline(scorer, db, config)
        market = _make_market()

        signal = await pipeline.update_position(market, prior_p=0.60)

        assert signal is None


# ---------------------------------------------------------------------------
# update_position() — ADD returns incremental signal
# ---------------------------------------------------------------------------


class TestUpdatePositionAdd:
    """update_position returns signal with same side when edge has grown."""

    @pytest.mark.asyncio
    async def test_update_position_add(self, db: Database) -> None:
        """Prior edge was 0.07, new edge is 0.22 (>2x) -> ADD."""
        config = _make_config()
        scorer = _make_scorer(probability=0.70)
        pipeline = BrainPipeline(scorer, db, config)
        market = _make_market()

        signal = await pipeline.update_position(market, prior_p=0.55)

        assert signal is not None
        assert signal.action == "BUY_YES"
        assert signal.position_size_usd > 0


# ---------------------------------------------------------------------------
# update_position() — scorer failure returns None
# ---------------------------------------------------------------------------


class TestUpdatePositionScorerFailure:
    """update_position returns None when scorer fails."""

    @pytest.mark.asyncio
    async def test_update_position_scorer_failure(self, db: Database) -> None:
        """Scorer returning None during position update should bail."""
        config = _make_config()
        scorer = MagicMock()
        scorer.estimate_probability.return_value = None
        pipeline = BrainPipeline(scorer, db, config)

        signal = await pipeline.update_position(_make_market(), prior_p=0.60)

        assert signal is None
