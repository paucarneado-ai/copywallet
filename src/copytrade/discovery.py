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
    """Score a wallet's resolved positions grouped by market category.

    Accepts two formats:
    - Closed positions (from Data API): has ``realizedPnl``, ``totalBought``, ``category``
    - Legacy test format: has ``status`` ("WON"/"LOST"), ``size``, ``price``, ``category``

    Returns:
        Mapping of category name to a stats dict with:
        ``win_rate``, ``resolved_positions``, ``roi``, ``profit_factor``,
        ``avg_position_size``, ``total_pnl``.
    """
    if not trades:
        return {}

    buckets: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        category = trade.get("category", "unknown")
        buckets.setdefault(category, []).append(trade)

    results: dict[str, dict[str, Any]] = {}
    for category, cat_trades in buckets.items():
        if category == "unknown":
            continue
        results[category] = _score_category(cat_trades)

    return results


def _score_category(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate statistics for a single category's positions.

    Supports both closed-positions format (realizedPnl) and legacy test
    format (status WON/LOST).
    """
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    total_size = 0.0

    for t in trades:
        # Closed-positions format from Polymarket API
        if "realizedPnl" in t:
            pnl = float(t["realizedPnl"])
            size = float(t.get("totalBought", 0))
            total_size += size
            if pnl > 0:
                wins += 1
                gross_profit += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)
        # Legacy test format
        elif "status" in t:
            size = float(t.get("size", 0))
            price = float(t.get("price", 0))
            total_size += size
            if t["status"] == "WON":
                wins += 1
                gross_profit += size * (1 - price)
            elif t["status"] == "LOST":
                losses += 1
                gross_loss += size * price

    total = wins + losses
    if total == 0:
        return {"win_rate": 0, "resolved_positions": 0, "roi": 0,
                "profit_factor": 0, "avg_position_size": 0, "total_pnl": 0}

    return {
        "win_rate": wins / total,
        "resolved_positions": total,
        "roi": (gross_profit - gross_loss) / max(gross_loss, 1),
        "profit_factor": gross_profit / max(gross_loss, 0.01),
        "avg_position_size": total_size / len(trades),
        "total_pnl": gross_profit - gross_loss,
    }


def is_suspicious(
    category_stats: dict[str, dict[str, Any]],
    max_win_rate: float = 0.90,
) -> bool:
    """Legacy binary suspicion check — kept for backward compatibility.

    Prefer :func:`score_wallet` which returns a richer tier + score.
    """
    tier, _ = score_wallet(category_stats, max_win_rate=max_win_rate)
    return tier == 4


def score_wallet(
    category_stats: dict[str, dict[str, Any]],
    total_pnl: float = 0.0,
    total_volume: float = 0.0,
    max_win_rate: float = 0.90,
) -> tuple[int, float]:
    """Score a wallet into quality tiers with a 0-100 quality score.

    Tier 1 (proven):       score >= 70, realistic stats, deep history
    Tier 2 (promising):    score >= 45, decent stats, moderate history
    Tier 3 (experimental): score >= 20, limited data or mixed signals
    Tier 4 (suspicious):   score < 20, stat-padding / wash trading signals

    Returns:
        Tuple of (tier, quality_score).
    """
    if not category_stats:
        return 4, 0.0

    red_flags = 0
    green_signals = 0

    # --- Aggregate across categories ---
    all_wrs: list[float] = []
    all_pfs: list[float] = []
    all_rois: list[float] = []
    all_positions: list[int] = []
    total_resolved = 0

    for stats in category_stats.values():
        wr = stats.get("win_rate", 0)
        pf = stats.get("profit_factor", 0)
        roi = stats.get("roi", 0)
        pos = stats.get("resolved_positions", 0)
        total_resolved += pos
        all_wrs.append(wr)
        all_pfs.append(pf)
        all_rois.append(roi)
        all_positions.append(pos)

    best_wr = max(all_wrs) if all_wrs else 0
    max_pf = max(all_pfs) if all_pfs else 0
    max_roi = max(all_rois) if all_rois else 0

    # --- Red flag checks (each adds 1-3 flags) ---
    #
    # NOTE: Polymarket Data API inflates PF/ROI because closed-positions
    # ``realizedPnl`` can be huge relative to ``totalBought`` for cheap
    # outcomes that resolve at $1.  And ``total_pnl`` (all-time) vs
    # ``total_volume`` (period-specific) are mismatched.  Thresholds
    # are calibrated for these API quirks.

    # Perfect or near-perfect WR with meaningful sample = stat-padding
    if best_wr >= 0.95 and total_resolved >= 10:
        red_flags += 3
    elif best_wr > max_win_rate and total_resolved < 100:
        red_flags += 2

    # PF and ROI are highly correlated on Polymarket (both spike from
    # the same cheap-outcome API quirk).  Score them together as a
    # single "metric inflation" signal to avoid double-penalizing.
    metric_flags = 0
    if max_pf > 1000 or max_roi > 1000:
        metric_flags = 3  # Almost certainly wash trading
    elif max_pf > 200 or max_roi > 200:
        metric_flags = 2
    elif max_pf > 50 or max_roi > 50:
        metric_flags = 1
    red_flags += metric_flags

    # PnL >> Volume — only flag extreme mismatches since
    # the API fields cover different time windows
    if total_volume > 0 and total_pnl / total_volume > 50:
        red_flags += 2
    elif total_volume > 0 and total_pnl / total_volume > 20:
        red_flags += 1

    # All categories at exactly pagination limit (truncated data)
    if all_positions and all(p in (48, 49, 50) for p in all_positions):
        red_flags += 1

    # --- Green signal checks ---

    # Realistic win rate band (55-75%)
    if 0.55 <= best_wr <= 0.75:
        green_signals += 3
    elif 0.50 <= best_wr <= 0.80:
        green_signals += 1

    # Deep sample size
    if total_resolved >= 200:
        green_signals += 3
    elif total_resolved >= 50:
        green_signals += 2
    elif total_resolved >= 20:
        green_signals += 1

    # Profitable wallet (PF > 1 means profitable overall)
    if max_pf > 1.0:
        green_signals += 1
    # Extra credit for PF in the "clearly real" band
    if 1.2 <= max_pf <= 50:
        green_signals += 1

    # Positive ROI
    if max_roi > 0:
        green_signals += 1
    # Extra credit for ROI in modest range
    if 0 < max_roi <= 20:
        green_signals += 1

    # Category specialist (1-3 categories = focused)
    num_cats = len(category_stats)
    if 1 <= num_cats <= 3:
        green_signals += 1

    # --- Compute score ---
    # Green signals worth +8 each, red flags cost -12 each.
    # Penalty is moderate so wallets with good WR + deep sample
    # can survive a couple of API-inflated metric flags.
    raw_score = (green_signals * 8) - (red_flags * 12)
    quality_score = max(0.0, min(100.0, float(raw_score)))

    # --- Assign tier ---
    if quality_score >= 70:
        tier = 1
    elif quality_score >= 45:
        tier = 2
    elif quality_score >= 20:
        tier = 3
    else:
        tier = 4

    return tier, quality_score


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
                    closed = await self._get_closed_positions_capped(address, max_pages=10)
                    enriched = self._categorize_closed_positions(closed, category)
                    cat_stats = score_wallet_trades(enriched)
                    pnl = float(leader.get("pnl", 0.0))
                    tier, quality_score = score_wallet(
                        cat_stats,
                        total_pnl=pnl,
                        total_volume=volume,
                        max_win_rate=self._config.max_win_rate,
                    )

                    wallet_data: dict[str, Any] = {
                        "address": address,
                        "username": username,
                        "category_stats": cat_stats,
                        "total_pnl": pnl,
                        "total_volume": volume,
                        "is_suspicious": tier == 4,
                        "tier": tier,
                        "quality_score": quality_score,
                        "last_scored": datetime.utcnow().isoformat(),
                    }

                    await self._db.upsert_wallet(wallet_data)
                    tracked_count += 1
                    logger.info(
                        "Scored %s: tier=%d score=%.0f PnL=$%.0f categories=%s",
                        username, tier, quality_score,
                        pnl, list(cat_stats.keys()),
                    )
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

    async def _get_closed_positions_capped(self, address: str, max_pages: int = 10) -> list[dict[str, Any]]:
        """Fetch closed positions with a cap on pagination."""
        all_positions: list[dict[str, Any]] = []
        for page in range(max_pages):
            try:
                batch = await self._api.get_closed_positions(address, limit=100, offset=page * 100)
            except Exception:
                break
            if not batch:
                break
            all_positions.extend(batch)
            if len(batch) < 100:
                break
        return all_positions

    def _categorize_closed_positions(
        self, positions: list[dict[str, Any]], default_category: str
    ) -> list[dict[str, Any]]:
        """Add a ``category`` field to closed positions based on title keywords."""
        crypto_keywords = ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol")
        sports_keywords = ("nfl", "nba", "soccer", "football", "match", "game", "championship")
        politics_keywords = ("election", "president", "congress", "vote", "political", "trump", "biden")

        for pos in positions:
            title = pos.get("title", "").lower()
            if any(kw in title for kw in crypto_keywords):
                pos["category"] = "crypto"
            elif any(kw in title for kw in sports_keywords):
                pos["category"] = "sports"
            elif any(kw in title for kw in politics_keywords):
                pos["category"] = "politics"
            else:
                pos["category"] = default_category.lower()
        return positions

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
