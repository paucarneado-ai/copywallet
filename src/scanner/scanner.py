"""Market scanner that discovers tradeable opportunities from Polymarket.

Fetches active events from the Gamma API, applies configurable filters
(volume, spread, time-to-resolution), and returns a list of
:class:`MarketOpportunity` objects for evaluation by the brain module.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.scanner.models import MarketOpportunity

if TYPE_CHECKING:
    from src.config import ScannerConfig
    from src.polymarket_api import PolymarketAPI
    from src.state.database import Database

logger = logging.getLogger(__name__)


class MarketScanner:
    """Scans Polymarket for active markets matching filter criteria.

    Args:
        api: Async client for Polymarket's Gamma and CLOB endpoints.
        db: Database instance for checking existing positions.
        config: Scanner filter thresholds (volume, spread, time).
    """

    def __init__(
        self,
        api: PolymarketAPI,
        db: Database,
        config: ScannerConfig,
    ) -> None:
        self._api = api
        self._db = db
        self._config = config

    async def scan(self) -> list[MarketOpportunity]:
        """Scan for active markets matching filter criteria.

        For each category in config.categories, fetches events from the
        Gamma API and filters each market by volume, time-to-resolution,
        and spread.  Markets that pass all filters are returned as
        :class:`MarketOpportunity` instances.
        """
        opportunities: list[MarketOpportunity] = []
        open_positions = await self._db.get_open_positions()
        position_market_ids = {p["market_id"] for p in open_positions}

        for category in self._config.categories:
            try:
                events = await self._api.get_events(limit=50, tag=category)
            except Exception as exc:
                logger.error("Failed to fetch events for %s: %s", category, exc)
                continue

            for event in events:
                for market in event.get("markets", [event]):
                    try:
                        opp = await self._process_market(
                            market, category, position_market_ids
                        )
                        if opp:
                            opportunities.append(opp)
                    except Exception as exc:
                        logger.debug("Skipping market: %s", exc)

        logger.info("Scanner found %d opportunities", len(opportunities))
        return opportunities

    async def _process_market(
        self,
        market: dict,
        category: str,
        position_market_ids: set,
    ) -> MarketOpportunity | None:
        """Evaluate a single market dict against all scanner filters.

        Returns a :class:`MarketOpportunity` when the market passes every
        filter, or ``None`` when any filter rejects it.
        """
        condition_id = market.get("conditionId", market.get("condition_id", ""))
        question = market.get("question", "")
        volume = float(market.get("volume24hr", market.get("volume", 0)))
        end_date = market.get("endDate", market.get("end_date", ""))
        neg_risk = market.get("negRisk", market.get("neg_risk", False))

        # Volume filter
        if volume < self._config.min_volume_24h:
            return None

        # Time-to-resolution filter
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                hours_remaining = (
                    end_dt - datetime.now(timezone.utc)
                ).total_seconds() / 3600
                if hours_remaining < self._config.min_time_to_resolution_hours:
                    return None
            except (ValueError, TypeError):
                pass  # skip filter when date is unparseable

        # Extract YES/NO token IDs
        token_yes, token_no = _extract_token_ids(market.get("tokens", []))
        if not token_yes:
            return None

        # Fetch live prices from CLOB
        try:
            yes_price = await self._api.get_midpoint(token_yes)
            spread = await self._api.get_spread(token_yes)
        except Exception:
            return None

        # Spread filter
        if spread > self._config.max_spread:
            return None

        no_price = 1.0 - yes_price
        is_existing = condition_id in position_market_ids

        return MarketOpportunity(
            market_id=condition_id,
            token_id_yes=token_yes,
            token_id_no=token_no,
            question=question,
            category=category,
            yes_price=yes_price,
            no_price=no_price,
            volume_24h=volume,
            spread=spread,
            end_date=end_date,
            neg_risk=neg_risk,
            is_existing_position=is_existing,
        )


def _extract_token_ids(tokens: list[dict]) -> tuple[str, str]:
    """Parse YES and NO token IDs from a Gamma-API tokens list.

    Returns:
        A (token_yes, token_no) tuple.  Either value may be an
        empty string if the corresponding outcome is missing.
    """
    token_yes = ""
    token_no = ""
    for tok in tokens:
        outcome = tok.get("outcome", "").upper()
        if outcome == "YES":
            token_yes = tok.get("token_id", "")
        elif outcome == "NO":
            token_no = tok.get("token_id", "")
    return token_yes, token_no
