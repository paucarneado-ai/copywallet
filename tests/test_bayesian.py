"""Tests for Bayesian probability updater with log-odds sequential updates."""

import math

from src.brain.bayesian import (
    bayesian_update,
    decide_position_action,
    from_log_odds,
    log_odds_update,
    to_log_odds,
)


class TestToLogOdds50Percent:
    """Verify that 50% probability maps to zero log-odds."""

    def test_to_log_odds_50_percent(self) -> None:
        """log-odds of 0.50 should be 0 since odds are 1:1."""
        assert abs(to_log_odds(0.50)) < 0.001


class TestRoundtripLogOdds:
    """Verify log-odds conversion round-trips back to the original probability."""

    def test_roundtrip_log_odds(self) -> None:
        """from_log_odds(to_log_odds(p)) should return p for several values."""
        for p in [0.1, 0.25, 0.5, 0.75, 0.9]:
            assert abs(from_log_odds(to_log_odds(p)) - p) < 0.001


class TestBayesianUpdateStrongEvidence:
    """Verify that strong evidence shifts the posterior substantially."""

    def test_bayesian_update_strong_evidence(self) -> None:
        """Prior=0.35 with P(E|H)=0.85, P(E|notH)=0.15 -> posterior > 0.60."""
        posterior = bayesian_update(0.35, 0.85, 0.15)
        assert posterior > 0.60


class TestBayesianUpdateWeakEvidence:
    """Verify that weak evidence only nudges the posterior slightly."""

    def test_bayesian_update_weak_evidence(self) -> None:
        """Prior=0.35 with P(E|H)=0.55, P(E|notH)=0.45 -> small shift."""
        posterior = bayesian_update(0.35, 0.55, 0.45)
        assert 0.35 < posterior < 0.45


class TestLogOddsUpdateMatchesBayes:
    """Verify that log-odds sequential update agrees with full Bayes."""

    def test_log_odds_update_matches_bayes(self) -> None:
        """Both methods should produce the same posterior within tolerance."""
        prior = 0.35
        lt, lf = 0.85, 0.15
        bayes_result = bayesian_update(prior, lt, lf)
        log_result = log_odds_update(prior, lt / lf)
        assert abs(bayes_result - log_result) < 0.01


class TestDecideActionHold:
    """Verify HOLD when edge is still positive but not dramatically larger."""

    def test_decide_action_hold(self) -> None:
        """Originally bought YES (prior > market), edge still present -> HOLD."""
        action = decide_position_action(
            prior_p=0.60, new_p=0.62, market_price=0.48, min_ev=0.05,
        )
        assert action == "HOLD"


class TestDecideActionExit:
    """Verify EXIT when the probability flips direction relative to market."""

    def test_decide_action_exit(self) -> None:
        """Originally bought YES but new_p dropped below market -> EXIT."""
        action = decide_position_action(
            prior_p=0.60, new_p=0.42, market_price=0.48, min_ev=0.05,
        )
        assert action == "EXIT"


class TestDecideActionAdd:
    """Verify ADD when the edge has grown to more than double the prior edge."""

    def test_decide_action_add(self) -> None:
        """Prior edge=0.07, new edge=0.22 (>2x) -> ADD."""
        action = decide_position_action(
            prior_p=0.55, new_p=0.70, market_price=0.48, min_ev=0.05,
        )
        assert action == "ADD"
