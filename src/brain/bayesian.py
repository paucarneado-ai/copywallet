"""Bayesian probability updater with log-odds sequential updates.

Provides pure functions for converting between probability and log-odds
representations, performing Bayesian updates, and deciding position actions
based on updated probability estimates versus market prices.
"""

import math


def to_log_odds(p: float) -> float:
    """Convert probability to log-odds.

    Clamps p to (0.01, 0.99) to avoid infinity at the boundaries.

    Args:
        p: Probability value between 0 and 1.

    Returns:
        Log-odds representation: ln(p / (1 - p)).
    """
    p = max(0.01, min(0.99, p))
    return math.log(p / (1 - p))


def from_log_odds(lo: float) -> float:
    """Convert log-odds back to probability.

    Args:
        lo: Log-odds value.

    Returns:
        Probability in range (0, 1).
    """
    return math.exp(lo) / (1 + math.exp(lo))


def bayesian_update(
    prior: float,
    likelihood_true: float,
    likelihood_false: float,
) -> float:
    """Full Bayes update: P(H|E) = P(E|H) * P(H) / P(E).

    Args:
        prior: P(H) -- prior probability of the hypothesis.
        likelihood_true: P(E|H) -- probability of evidence if hypothesis true.
        likelihood_false: P(E|not H) -- probability of evidence if hypothesis false.

    Returns:
        Posterior probability P(H|E). Returns prior unchanged when
        the total evidence probability (denominator) is zero.
    """
    numerator = likelihood_true * prior
    denominator = numerator + likelihood_false * (1 - prior)

    if denominator == 0:
        return prior

    return numerator / denominator


def log_odds_update(prior: float, likelihood_ratio: float) -> float:
    """Sequential update using log-odds (addition instead of multiplication).

    Converts prior to log-odds, adds the log of the likelihood ratio,
    and converts back. This is numerically more stable for chaining
    multiple updates sequentially.

    Args:
        prior: Prior probability P(H).
        likelihood_ratio: P(E|H) / P(E|not H). Clamped to min 0.01
            to avoid log(0).

    Returns:
        Posterior probability after the log-odds update.
    """
    likelihood_ratio = max(0.01, likelihood_ratio)
    log_prior = to_log_odds(prior)
    log_posterior = log_prior + math.log(likelihood_ratio)
    return from_log_odds(log_posterior)


def decide_position_action(
    prior_p: float,
    new_p: float,
    market_price: float,
    min_ev: float = 0.05,
) -> str:
    """Decide what to do with an existing position based on updated probability.

    Determines the original trade direction from the prior, then checks
    whether the updated probability still supports that direction and
    whether the edge has grown, shrunk, or flipped.

    Args:
        prior_p: Probability estimate when the position was opened.
        new_p: Updated probability estimate after new evidence.
        market_price: Current market price (implied probability).
        min_ev: Minimum expected-value edge to justify holding.

    Returns:
        One of "EXIT", "ADD", or "HOLD".
    """
    was_yes = prior_p > market_price
    now_yes = new_p > market_price

    # Direction flipped -- original thesis invalidated
    if was_yes != now_yes:
        return "EXIT"

    prior_ev = abs(prior_p - market_price)
    new_ev = abs(new_p - market_price)

    # Edge too thin to justify the position
    if new_ev < min_ev:
        return "EXIT"

    # Edge more than doubled -- add to the position
    if prior_ev > 0 and new_ev > 2 * prior_ev:
        return "ADD"

    return "HOLD"
