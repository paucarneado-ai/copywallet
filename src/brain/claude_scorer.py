"""Claude-powered probability scorer for prediction market outcomes.

Uses the Anthropic Claude API with tool_use for structured JSON output.
The prompt deliberately excludes the current market price to prevent
anchoring bias -- Claude estimates probability independently.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Category-specific framing for the estimation prompt.
_CATEGORY_CONTEXT: dict[str, str] = {
    "crypto": (
        "This is a cryptocurrency price prediction market. Consider recent "
        "price action, market sentiment, volatility patterns, and on-chain "
        "metrics. Crypto markets are highly volatile -- be cautious with "
        "extreme probabilities."
    ),
    "politics": (
        "This is a political prediction market. Consider polling data, "
        "historical base rates, incumbency effects, and political dynamics. "
        "Political events often have more uncertainty than people think."
    ),
    "sports": (
        "This is a sports prediction market. Consider team form, head-to-head "
        "records, injuries, and home/away advantage. Sports outcomes have "
        "significant randomness."
    ),
    "economics": (
        "This is an economics prediction market. Consider leading indicators, "
        "central bank signaling, consensus forecasts, and historical patterns. "
        "Economic data can surprise."
    ),
    "weather": (
        "This is a weather prediction market. Consider meteorological model "
        "outputs (GFS, ECMWF), historical climate data, and forecast "
        "uncertainty ranges."
    ),
}

_DEFAULT_CATEGORY_CONTEXT = (
    "Consider base rates, relevant evidence, and historical patterns. "
    "Be calibrated -- avoid extreme probabilities without strong evidence."
)

# Tool definition for structured JSON output from Claude.
_PROBABILITY_TOOL: dict[str, Any] = {
    "name": "probability_estimate",
    "description": (
        "Provide your calibrated probability estimate for this "
        "prediction market outcome."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "probability": {
                "type": "number",
                "description": (
                    "True probability between 0.00 and 1.00. If you say "
                    "0.70, approximately 7 out of 10 such predictions "
                    "should resolve YES."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "Your confidence in this estimate. Low = high "
                    "uncertainty, Medium = reasonable estimate, "
                    "High = strong evidence-based estimate."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Brief explanation of your reasoning, including key "
                    "factors and base rates considered."
                ),
            },
        },
        "required": ["probability", "confidence", "reasoning"],
    },
}


class ClaudeScorer:
    """Estimates true probabilities for prediction market outcomes using Claude.

    CRITICAL DESIGN DECISION: The prompt does NOT include the current market
    price. This prevents anchoring bias -- Claude should estimate probability
    independently of what the market thinks.

    When ``api_key`` is empty or the ``anthropic`` package is not installed,
    all scoring methods gracefully return ``None`` so the rest of the
    pipeline can operate without Claude.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        """Initialise the scorer, lazily importing the anthropic SDK.

        Args:
            api_key: Anthropic API key. If empty, the scorer is disabled
                and all methods return ``None``.
            model: Claude model identifier to use for estimation calls.
        """
        self._client: Any | None = None
        self._model = model

        if api_key:
            try:
                import anthropic  # noqa: WPS433 (nested import is intentional)

                self._client = anthropic.Anthropic(api_key=api_key)
            except ImportError:
                logger.warning(
                    "anthropic package not installed. Claude scorer disabled."
                )

    def is_available(self) -> bool:
        """Return whether the scorer has a working API client."""
        return self._client is not None

    def estimate_probability(
        self,
        question: str,
        category: str,
        context: str = "",
    ) -> dict[str, Any] | None:
        """Ask Claude to estimate the true probability of a market outcome.

        Args:
            question: The prediction market question text.
            category: Market category (e.g. "crypto", "politics").
            context: Optional additional context for the estimation.

        Returns:
            A dict with keys ``probability`` (float, 0.01-0.99),
            ``confidence`` ("low"/"medium"/"high"), and ``reasoning``
            (str), or ``None`` if the scorer is disabled or the call fails.

        Uses tool_use for structured JSON output.
        """
        if not self._client:
            return None

        prompt = _build_prompt(question, category, context)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                tools=[_PROBABILITY_TOOL],
                tool_choice={
                    "type": "tool",
                    "name": "probability_estimate",
                },
                messages=[{"role": "user", "content": prompt}],
            )
            return _extract_result(response)

        except Exception:
            logger.exception("Claude API call failed")
            return None


# ---------------------------------------------------------------------------
# Pure helpers (module-level for testability)
# ---------------------------------------------------------------------------


def _extract_result(response: Any) -> dict[str, Any] | None:
    """Pull the probability_estimate tool-use block from a Claude response.

    Clamps probability to (0.01, 0.99) to prevent division-by-zero in
    downstream Kelly / EV calculations.
    """
    for block in response.content:
        if block.type == "tool_use" and block.name == "probability_estimate":
            result = block.input
            prob = float(result.get("probability", 0.5))
            prob = max(0.01, min(0.99, prob))

            return {
                "probability": prob,
                "confidence": result.get("confidence", "medium"),
                "reasoning": result.get("reasoning", ""),
            }

    logger.warning(
        "Claude response did not contain probability_estimate tool use"
    )
    return None


def _build_prompt(question: str, category: str, context: str) -> str:
    """Build the estimation prompt WITHOUT market price (anti-anchoring).

    Category-specific framing adjusts the system context:
    - crypto: short-term price action, volatility, market microstructure
    - politics: polling data, historical base rates, incumbency effects
    - sports: team stats, form, head-to-head records
    - economics: leading indicators, Fed signaling, consensus forecasts
    - weather: meteorological models, climate data
    - default: general forecasting principles
    """
    cat_prompt = _CATEGORY_CONTEXT.get(
        category.lower(), _DEFAULT_CATEGORY_CONTEXT
    )

    parts = [
        "You are a calibrated prediction market analyst. Your job is to "
        "estimate the TRUE probability of outcomes.",
        "",
        f"Market question: {question}",
        f"Category: {category}",
    ]

    if context:
        parts.append(f"Additional context: {context}")

    parts.extend(
        [
            "",
            cat_prompt,
            "",
            "CALIBRATION RULES:",
            "- If you say 70%, approximately 7 out of 10 such predictions "
            "should resolve YES.",
            "- Penalize extreme confidence. Very few events are truly >90% "
            "or <10%.",
            "- Consider base rates before adjusting for specific evidence.",
            "- Uncertainty is honest. When genuinely uncertain, stay closer "
            "to 50%.",
            "",
            "Estimate the true probability now.",
        ]
    )

    return "\n".join(parts)
