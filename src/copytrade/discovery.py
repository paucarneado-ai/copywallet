"""Wallet discovery service for copy trading signal identification.

Scans the Polymarket leaderboard to find profitable wallets, scores
their per-category trading performance, and flags stat-padded accounts.
Enriches trades with market categories from the Gamma API (with caching)
before persisting scored profiles to the database.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.config import CopyTradingConfig
from src.polymarket_api import PolymarketAPI
from src.state.database import Database

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure functions (no side effects, fully testable without mocking)
# ---------------------------------------------------------------------------


def score_wallet_trades(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Score a wallet's resolved trades grouped by market category.

    Each trade dict must contain keys: ``outcome``, ``side``, ``price``,
    ``size``, ``status`` (``"WON"`` or ``"LOST"``), and ``category``.

    Returns:
        Mapping of category name to a stats dict with:
        ``win_rate``, ``resolved_positions``, ``roi``, ``profit_factor``,
        ``avg_position_size``, ``total_pnl``.
    """
    if not trades:
        return {}

    buckets: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        category = trade["category"]
        buckets.setdefault(category, []).append(trade)

    results: dict[str, dict[str, Any]] = {}
    for category, cat_trades in buckets.items():
        results[category] = _score_category(cat_trades)

    return results


def _score_category(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate statistics for a single category's trades.

    Win/loss classification uses the ``status`` field.  Profit and loss
    amounts derive from ``size`` (USD notional) and ``price`` (entry
    probability).
    """
    wins = [t for t in trades if t["status"] == "WON"]
    losses = [t for t in trades if t["status"] == "LOST"]

    total = len(wins) + len(losses)
    win_rate = len(wins) / total if total > 0 else 0.0

    gross_profit = sum(t["size"] * (1 - t["price"]) for t in wins)
    gross_loss = sum(t["size"] * t["price"] for t in losses)

    roi = (gross_profit - gross_loss) / max(gross_loss, 1)
    profit_factor = gross_profit / max(gross_loss, 0.01)
    avg_position_size = sum(t["size"] for t in trades) / len(trades)
    total_pnl = gross_profit - gross_loss

    return {
        "win_rate": win_rate,
        "resolved_positions": total,
        "roi": roi,
        "profit_factor": profit_factor,
        "avg_position_size": avg_position_size,
        "total_pnl": total_pnl,
    }


def is_suspicious(
    category_stats: dict[str, dict[str, Any]],
    max_win_rate: float = 0.90,
) -> bool:
    """Detect wallets with artificially inflated statistics.

    A wallet is suspicious when any category shows a win rate strictly
    above *max_win_rate* combined with fewer than 100 resolved positions.
    This pattern typically indicates stat-padding with tiny, low-risk bets.

    Args:
        category_stats: mapping of category to stats dicts, each containing
            at least ``win_rate`` and ``resolved_positions``.
        max_win_rate: upper bound before suspicion triggers (exclusive).

    Returns:
        ``True`` when at least one category triggers the heuristic.
    """
    for stats in category_stats.values():
        if stats["win_rate"] > max_win_rate and stats["resolved_positions"] < 100:
            return True
    return False


# ---------------------------------------------------------------------------
# Async discovery service (depends on API + DB)
# ---------------------------------------------------------------------------

_LEADERBOARD_LIMIT = 50


class WalletDiscovery:
    """Discover and score profitable leader wallets from Polymarket.

    Fetches leaderboard data, enriches trades with market categories,
    scores each wallet per category, filters out suspicious accounts,
    and persists results to the database for downstream copy trading.

    Args:
        api: Polymarket API client for fetching leaderboard and trades.
        db: Database handle for wallet profiles and market cache.
        config: Copy trading configuration with thresholds.
    """

    def __init__(
        self,
        api: PolymarketAPI,
        db: Database,
        config: CopyTradingConfig,
    ) -> None:
        self._api = api
        self._db = db
        self._config = config

    async def discover(self) -> None:
        """Run the full wallet discovery loop.

        For each configured category, fetches the top leaderboard wallets,
        retrieves their trade histories, scores them, checks for suspicion,
        and upserts the results into the database.
        """
        tracked_count = 0

        for category in self._config.categories:
            logger.info("Discovering wallets for category: %s", category)

            leaders = await self._api.get_leaderboard(
                category=category,
                period="MONTH",
                order_by="PNL",
                limit=_LEADERBOARD_LIMIT,
            )

            for leader in leaders:
                volume = float(leader.get("vol", leader.get("volume", 0)))
                if volume < self._config.min_volume:
                    continue

                address = leader.get("proxyWallet", leader.get("address", ""))
                if not address:
                    continue
                username = leader.get("userName", leader.get("username", address[:8]))

                try:
                    raw_trades = await self._get_trades_capped(address, max_pages=10)
                    enriched = await self._enrich_trades_with_category(raw_trades)
                    cat_stats = score_wallet_trades(enriched)
                    suspicious = is_suspicious(cat_stats, self._config.max_win_rate)

                    wallet_data: dict[str, Any] = {
                        "address": address,
                        "username": username,
                        "category_stats": cat_stats,
                        "total_pnl": float(leader.get("pnl", 0.0)),
                        "total_volume": volume,
                        "is_suspicious": suspicious,
                        "last_scored": datetime.utcnow().isoformat(),
                    }

                    await self._db.upsert_wallet(wallet_data)
                    tracked_count += 1
                    logger.info("Scored %s: PnL=$%.0f, categories=%s, suspicious=%s",
                                username, float(leader.get("pnl", 0)), list(cat_stats.keys()), suspicious)
                except Exception as exc:
                    logger.warning("Failed to score wallet %s: %s", address[:10], exc)
                    continue

                if tracked_count >= self._config.max_tracked_wallets:
                    logger.info(
                        "Reached max tracked wallets (%d), stopping discovery",
                        self._config.max_tracked_wallets,
                    )
                    return

        logger.info("Discovery complete: %d wallets tracked", tracked_count)

    async def _get_trades_capped(self, address: str, max_pages: int = 10) -> list[dict[str, Any]]:
        """Fetch trades with a cap on pagination to avoid API errors on heavy traders."""
        all_trades: list[dict[str, Any]] = []
        for page in range(max_pages):
            try:
                batch = await self._api.get_trades(address, limit=100, offset=page * 100)
            except Exception:
                break
            if not batch:
                break
            all_trades.extend(batch)
            if len(batch) < 100:
                break
        return all_trades

    async def _enrich_trades_with_category(
        self,
        trades: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Add a ``category`` field to each trade from market metadata.

        Looks up the market's condition_id in the database cache first.
        On a cache miss, fetches from the Gamma API, extracts the first
        tag label as the category, and caches the result for future use.

        Args:
            trades: raw trade dicts, each containing a ``condition_id`` key.

        Returns:
            The same trade dicts with a ``category`` key added.
        """
        for trade in trades:
            condition_id = trade.get("condition_id", "")
            if not condition_id:
                trade["category"] = "UNKNOWN"
                continue

            cached = await self._db.get_cached_market(condition_id)
            if cached:
                trade["category"] = cached.get("category", "UNKNOWN")
                continue

            try:
                market = await self._api.get_market(condition_id)
                tags = market.get("tags", [])
                category = tags[0].get("label", "UNKNOWN") if tags else "UNKNOWN"

                cache_entry: dict[str, Any] = {
                    "market_id": condition_id,
                    "condition_id": condition_id,
                    "token_id_yes": market.get("token_id_yes", ""),
                    "token_id_no": market.get("token_id_no", ""),
                    "question": market.get("question", ""),
                    "category": category,
                    "neg_risk": market.get("neg_risk", False),
                    "cached_at": datetime.utcnow().isoformat(),
                }
                await self._db.cache_market(cache_entry)

                trade["category"] = category
            except Exception:
                logger.warning(
                    "Failed to fetch market metadata for %s",
                    condition_id,
                    exc_info=True,
                )
                trade["category"] = "UNKNOWN"

        return trades
