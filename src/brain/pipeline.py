"""Brain pipeline orchestrating scorer, EV calculation, Kelly sizing, and Bayesian updates.

Coordinates the full decision flow for a single market: cooldown check,
Claude probability scoring, expected-value filtering, confidence-adjusted
Kelly sizing, and result caching.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.brain.bayesian import decide_position_action
from src.brain.ev_calculator import calculate_ev, determine_side, should_trade
from src.brain.models import TradeSignal
from src.config import Config
from src.executor.kelly import kelly_size
from src.scanner.models import MarketOpportunity
from src.state.database import Database

logger = logging.getLogger(__name__)

CONFIDENCE_MULTIPLIERS = {"high": 1.0, "medium": 0.6, "low": 0.3}


class BrainPipeline:
    """Orchestrates the full brain decision pipeline for market evaluation.

    Wires together a probability scorer, EV calculator, Kelly sizer,
    and Bayesian updater into a single ``evaluate()`` call per market.

    Args:
        scorer: Any object exposing ``estimate_probability(question, category) -> dict | None``.
        db: Async SQLite database for cooldown caching.
        config: Application configuration with risk and fee parameters.
    """

    def __init__(self, scorer: object, db: Database, config: Config) -> None:
        self._scorer = scorer
        self._db = db
        self._config = config
        self._cooldown_minutes: int = 15
        self._min_ev: float = 0.05

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(self, market: MarketOpportunity) -> Optional[TradeSignal]:
        """Run the full brain pipeline for a single market.

        Steps: cooldown check -> score -> side determination -> EV filter
        -> Kelly sizing -> cache -> build signal.

        Returns:
            A ``TradeSignal`` when the market offers sufficient edge,
            or ``None`` when skipped for any reason (cooldown, low EV,
            scorer failure, etc.).
        """
        if await self._check_cooldown(market.market_id):
            logger.debug("Skipping %s — within cooldown window", market.market_id)
            return None

        estimate = self._scorer.estimate_probability(market.question, market.category)
        if estimate is None:
            logger.warning("Scorer returned None for %s", market.market_id)
            return None

        p_true: float = estimate["probability"]
        confidence: str = estimate.get("confidence", "medium")
        reasoning: str = estimate.get("reasoning", "")

        side = determine_side(p_true, market.yes_price)
        if side == "HOLD":
            logger.debug("HOLD for %s — p_true ~= market price", market.market_id)
            return None

        fee_rate = self._config.fees.get(
            market.category, self._config.fees.get("default", 0.01)
        )

        ev = calculate_ev(p_true, market.yes_price, side, fee_rate)

        if not should_trade(ev, self._min_ev):
            logger.debug("EV %.4f below threshold for %s", ev, market.market_id)
            return None

        position_size = self._compute_position_size(p_true, market, side, confidence)
        if position_size <= 0:
            logger.debug("Kelly returned 0 for %s", market.market_id)
            return None

        await self._cache_evaluation(market.market_id)

        token_id = market.token_id_yes if side == "BUY_YES" else market.token_id_no
        adjusted_kelly = (
            self._config.risk.kelly_fraction
            * CONFIDENCE_MULTIPLIERS.get(confidence, 0.6)
        )

        return TradeSignal(
            market_id=market.market_id,
            token_id=token_id,
            question=market.question,
            category=market.category,
            action=side,
            p_true=p_true,
            p_market=market.yes_price,
            ev=ev,
            position_size_usd=position_size,
            kelly_fraction=adjusted_kelly,
            confidence=confidence,
            reasoning=reasoning,
            neg_risk=market.neg_risk,
        )

    async def update_position(
        self, market: MarketOpportunity, prior_p: float
    ) -> Optional[TradeSignal]:
        """Bayesian re-evaluation for an existing position.

        Asks the scorer for a fresh probability, runs
        ``decide_position_action`` against the prior, and returns an
        appropriate signal (SELL on EXIT, side-specific on ADD, None on HOLD).
        """
        estimate = self._scorer.estimate_probability(market.question, market.category)
        if estimate is None:
            logger.warning("Scorer returned None for position update on %s", market.market_id)
            return None

        new_p: float = estimate["probability"]
        confidence: str = estimate.get("confidence", "medium")
        reasoning: str = estimate.get("reasoning", "")

        action = decide_position_action(prior_p, new_p, market.yes_price, self._min_ev)

        if action == "HOLD":
            return None

        if action == "EXIT":
            return TradeSignal(
                market_id=market.market_id,
                token_id=market.token_id_yes,
                question=market.question,
                category=market.category,
                action="SELL",
                p_true=new_p,
                p_market=market.yes_price,
                ev=0.0,
                position_size_usd=0.0,
                kelly_fraction=0.0,
                confidence=confidence,
                reasoning=reasoning,
                neg_risk=market.neg_risk,
            )

        # ADD — compute incremental size using the updated probability
        side = determine_side(new_p, market.yes_price)
        if side == "HOLD":
            return None

        position_size = self._compute_position_size(new_p, market, side, confidence)
        token_id = market.token_id_yes if side == "BUY_YES" else market.token_id_no

        return TradeSignal(
            market_id=market.market_id,
            token_id=token_id,
            question=market.question,
            category=market.category,
            action=side,
            p_true=new_p,
            p_market=market.yes_price,
            ev=abs(new_p - market.yes_price),
            position_size_usd=position_size,
            kelly_fraction=self._config.risk.kelly_fraction
            * CONFIDENCE_MULTIPLIERS.get(confidence, 0.6),
            confidence=confidence,
            reasoning=reasoning,
            neg_risk=market.neg_risk,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_position_size(
        self,
        p_true: float,
        market: MarketOpportunity,
        side: str,
        confidence: str,
    ) -> float:
        """Kelly-size a position with confidence adjustment.

        For BUY_YES the probability/price pair is (p_true, yes_price).
        For BUY_NO the probability/price pair is (1 - p_true, no_price)
        because ``kelly_size`` expects edge = p - price > 0.
        """
        multiplier = CONFIDENCE_MULTIPLIERS.get(confidence, 0.6)
        adjusted_kelly = self._config.risk.kelly_fraction * multiplier

        if side == "BUY_YES":
            prob = p_true
            price = market.yes_price
        else:
            prob = 1.0 - p_true
            price = market.no_price

        return kelly_size(
            p_true=prob,
            market_price=price,
            bankroll=self._config.risk.starting_bankroll,
            kelly_fraction=adjusted_kelly,
            max_position_pct=self._config.risk.max_position_pct,
            cash_reserve_pct=self._config.risk.cash_reserve_pct,
            min_trade_size=self._config.risk.min_trade_size,
        )

    async def _check_cooldown(self, market_id: str) -> bool:
        """Return True if market was evaluated within the cooldown window.

        Uses the ``cached_at`` column of the ``market_cache`` table as
        the timestamp of the most recent evaluation.
        """
        row = await self._db.fetch_one(
            "SELECT cached_at FROM market_cache WHERE market_id = ?",
            (market_id,),
        )
        if not row or not row.get("cached_at"):
            return False

        try:
            last = datetime.fromisoformat(row["cached_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed_minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
            return elapsed_minutes < self._cooldown_minutes
        except (ValueError, TypeError):
            return False

    async def _cache_evaluation(self, market_id: str) -> None:
        """Upsert a market_cache row so cooldown tracking works."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT OR REPLACE INTO market_cache
               (market_id, condition_id, token_id_yes, token_id_no,
                question, category, neg_risk, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (market_id, market_id, "", "", "", "", 0, now),
        )
