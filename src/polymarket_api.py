"""Centralized async client for Polymarket's public APIs.

Wraps the Data, Gamma, and CLOB endpoints behind a single class so
that every outbound request flows through the shared rate limiter.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base URLs
# ---------------------------------------------------------------------------

DATA_API: str = "https://data-api.polymarket.com/v1"
GAMMA_API: str = "https://gamma-api.polymarket.com"
CLOB_API: str = "https://clob.polymarket.com"

_TIMEOUT_SECONDS: float = 15.0
_PAGE_SIZE: int = 100


class PolymarketAPI:
    """Async HTTP facade for Polymarket's read-only endpoints.

    All requests are throttled by a :class:`RateLimiter` so that the
    caller never has to worry about exceeding the 100-req/min ceiling.

    Args:
        rate_limiter: optional pre-configured limiter; a default
            ``RateLimiter()`` is created when *None*.
    """

    def __init__(self, rate_limiter: RateLimiter | None = None) -> None:
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
        )
        self._limiter: RateLimiter = rate_limiter or RateLimiter()

    async def close(self) -> None:
        """Shut down the underlying HTTP transport."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Rate-limited GET request returning parsed JSON.

        Raises:
            httpx.HTTPStatusError: on 4xx / 5xx responses.
        """
        await self._limiter.acquire()
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Data API
    # ------------------------------------------------------------------

    async def get_leaderboard(
        self,
        category: str,
        period: str,
        order_by: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch ranked traders from the Data API leaderboard.

        Args:
            category: market category (e.g. ``"CRYPTO"``).
            period: time window (e.g. ``"ALL"``).
            order_by: ranking field (e.g. ``"profit"``).
            limit: maximum results per page.
            offset: pagination offset.
        """
        params = {
            "category": category,
            "period": period,
            "orderBy": order_by,
            "limit": limit,
            "offset": offset,
        }
        result = await self._get(f"{DATA_API}/leaderboard", params=params)
        return result  # type: ignore[return-value]

    async def get_activity(
        self,
        user: str,
        trade_type: str = "TRADE",
        limit: int = 10,
        start: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve recent activity for a wallet address.

        Args:
            user: wallet address (lowercased automatically).
            trade_type: activity filter (default ``"TRADE"``).
            limit: maximum records.
            start: optional unix timestamp lower-bound.
        """
        params: dict[str, Any] = {
            "user": user.lower(),
            "type": trade_type,
            "limit": limit,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        if start is not None:
            params["start"] = start
        result = await self._get(f"{DATA_API}/activity", params=params)
        return result  # type: ignore[return-value]

    async def get_trades(
        self,
        user: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch a single page of trades for *user*.

        Args:
            user: wallet address (lowercased automatically).
            limit: page size (max 100).
            offset: pagination offset.
        """
        params: dict[str, Any] = {
            "user": user.lower(),
            "limit": limit,
            "offset": offset,
        }
        result = await self._get(f"{DATA_API}/trades", params=params)
        return result  # type: ignore[return-value]

    async def get_positions(
        self,
        user: str,
    ) -> list[dict[str, Any]]:
        """Fetch open positions for *user*.

        Args:
            user: wallet address (lowercased automatically).
        """
        params: dict[str, Any] = {"user": user.lower()}
        result = await self._get(f"{DATA_API}/positions", params=params)
        return result  # type: ignore[return-value]

    async def get_closed_positions(
        self,
        user: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch resolved/closed positions for *user*.

        Args:
            user: wallet address (lowercased automatically).
            limit: page size (max 100).
            offset: pagination offset.
        """
        params: dict[str, Any] = {
            "user": user.lower(),
            "limit": limit,
            "offset": offset,
        }
        result = await self._get(
            f"{DATA_API}/closed-positions", params=params
        )
        return result  # type: ignore[return-value]

    async def get_all_trades(
        self,
        user: str,
    ) -> list[dict[str, Any]]:
        """Paginate through every trade for *user*.

        Loops with offset increments of 100 until a batch returns
        fewer than 100 records.

        Args:
            user: wallet address (lowercased automatically).
        """
        all_trades: list[dict[str, Any]] = []
        offset = 0
        while True:
            batch = await self.get_trades(user, limit=_PAGE_SIZE, offset=offset)
            all_trades.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        logger.debug(
            "Fetched %d total trades for %s", len(all_trades), user.lower()
        )
        return all_trades

    # ------------------------------------------------------------------
    # Gamma API
    # ------------------------------------------------------------------

    async def get_market(
        self,
        condition_id: str,
    ) -> dict[str, Any]:
        """Fetch market metadata from the Gamma API.

        Args:
            condition_id: the market's condition identifier.

        Raises:
            ValueError: when the API returns no matching market, which
                happens when the conditionId is invalid or the Gamma API
                returns unfiltered results (a known quirk).
        """
        # Gamma API requires conditionId as query param, not path param
        results = await self._get(f"{GAMMA_API}/markets", {"conditionId": condition_id})
        if isinstance(results, list):
            # Gamma sometimes returns the full market list for bad conditionIds.
            # Validate that the result actually matches what we asked for.
            for market in results:
                if market.get("conditionId") == condition_id:
                    return market
            # No match — the conditionId was likely invalid
            logger.warning(
                "Gamma returned %d results for conditionId=%s but none matched",
                len(results),
                condition_id[:20],
            )
            raise ValueError(f"No Gamma market found for conditionId={condition_id[:20]}")
        return results  # type: ignore[return-value]

    async def get_events(
        self,
        limit: int = 50,
        offset: int = 0,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """List active events from the Gamma API.

        Args:
            limit: maximum results per page.
            offset: pagination offset.
            tag: optional category tag filter.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": True,
            "closed": False,
        }
        if tag is not None:
            params["tag"] = tag
        result = await self._get(f"{GAMMA_API}/events", params=params)
        return result  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # CLOB API
    # ------------------------------------------------------------------

    async def get_price(
        self,
        token_id: str,
    ) -> dict[str, Any]:
        """Fetch the current price object for an outcome token.

        Args:
            token_id: the CLOB token identifier.
        """
        params: dict[str, Any] = {"token_id": token_id}
        result = await self._get(f"{CLOB_API}/price", params=params)
        return result  # type: ignore[return-value]

    async def get_book(
        self,
        token_id: str,
    ) -> dict[str, Any]:
        """Fetch the order book for an outcome token.

        Args:
            token_id: the CLOB token identifier.
        """
        params: dict[str, Any] = {"token_id": token_id}
        result = await self._get(f"{CLOB_API}/book", params=params)
        return result  # type: ignore[return-value]

    async def get_midpoint(
        self,
        token_id: str,
    ) -> float:
        """Return the mid-price for an outcome token.

        Args:
            token_id: the CLOB token identifier.
        """
        data = await self.get_price(token_id)
        return float(data["mid"])

    async def get_spread(
        self,
        token_id: str,
    ) -> float:
        """Return the bid-ask spread for an outcome token.

        Args:
            token_id: the CLOB token identifier.
        """
        data = await self.get_price(token_id)
        return float(data["spread"])
