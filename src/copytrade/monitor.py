"""Round-robin trade monitor for copy trading signal discovery.

Polls tracked leader wallets in round-robin order, evaluates each new
trade through the filter pipeline, sizes positions with Kelly criterion,
and emits actionable :class:`CopySignal` instances.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.config import Config
from src.copytrade.filters import apply_filters
from src.copytrade.models import CopySignal
from src.executor.kelly import kelly_size
from src.executor.slippage import calculate_slippage, estimate_fill_price
from src.polymarket_api import PolymarketAPI
from src.state.database import Database

logger = logging.getLogger(__name__)


class RoundRobinPoller:
    """Cycles through a list of wallet addresses in round-robin order.

    Each call to :meth:`next_wallet` returns the next address and
    advances the internal index, wrapping at the end of the list.
    """

    def __init__(self, wallets: list[str]) -> None:
        self._wallets: list[str] = list(wallets)
        self._index: int = 0

    def next_wallet(self) -> str | None:
        """Return the next wallet address, or ``None`` when the list is empty."""
        if not self._wallets:
            return None
        wallet = self._wallets[self._index % len(self._wallets)]
        self._index = (self._index + 1) % len(self._wallets)
        return wallet

    def update_wallets(self, wallets: list[str]) -> None:
        """Replace the wallet list and reset the cycle index to zero."""
        self._wallets = list(wallets)
        self._index = 0


class CopyTradeMonitor:
    """Discovers copy trading signals by polling leader wallets.

    Coordinates the Data API, Gamma API, CLOB API, database, filter
    pipeline, and Kelly position sizer into a single async workflow.

    Args:
        api: Polymarket API client for market data.
        db: Database for trade deduplication and market cache.
        config: Application configuration with copy trading parameters.
    """

    def __init__(self, api: PolymarketAPI, db: Database, config: Config) -> None:
        self._api = api
        self._db = db
        self._config = config
        self._poller = RoundRobinPoller([])
        self._wallet_tiers: dict[str, int] = {}

    async def refresh_wallet_list(self) -> None:
        """Read all wallets from DB and update the poller.

        All wallets (tiers 1-4) are polled for data collection.
        Tier info is cached for Kelly sizing decisions during trade evaluation.
        Caps the wallet list at ``max_tracked_wallets`` from config.
        """
        try:
            wallets = await self._db.get_all_wallets()
            max_wallets = self._config.copy_trading.max_tracked_wallets
            wallets = wallets[:max_wallets]
            addresses = [w["address"] for w in wallets]
            self._wallet_tiers = {
                w["address"]: w.get("tier", 4) for w in wallets
            }
            self._poller.update_wallets(addresses)
            tier_counts = {}
            for t in self._wallet_tiers.values():
                tier_counts[t] = tier_counts.get(t, 0) + 1
            logger.info(
                "Refreshed wallet list: %d wallets (T1=%d T2=%d T3=%d T4=%d)",
                len(addresses),
                tier_counts.get(1, 0),
                tier_counts.get(2, 0),
                tier_counts.get(3, 0),
                tier_counts.get(4, 0),
            )
        except Exception:
            logger.exception("Failed to refresh wallet list")

    async def poll_next(self) -> list[CopySignal]:
        """Poll the next wallet in round-robin order for new trades.

        Returns:
            List of actionable :class:`CopySignal` for trades that pass
            all filters and have non-zero Kelly sizing.
        """
        wallet = self._poller.next_wallet()
        if wallet is None:
            return []

        try:
            activity = await self._api.get_activity(wallet)
        except Exception:
            logger.exception("Failed to fetch activity for %s", wallet)
            return []

        signals: list[CopySignal] = []
        for trade in activity:
            trade_id = trade.get("transactionHash", trade.get("id", ""))
            try:
                if await self._db.is_known_trade(trade_id):
                    continue
                await self._db.mark_trade_known(trade_id, wallet)
            except Exception:
                logger.exception("DB error checking/marking trade %s", trade_id)
                continue

            signal = await self._evaluate_trade(trade, wallet)
            if signal is not None:
                signals.append(signal)

        return signals

    async def _evaluate_trade(
        self, trade: dict[str, Any], leader_address: str
    ) -> CopySignal | None:
        """Evaluate a single leader trade through the full signal pipeline.

        Steps: extract fields, fetch market metadata, retrieve leader
        category stats, get current pricing, run filter pipeline, size
        with Kelly, and build the final signal.

        Returns:
            A :class:`CopySignal` if all filters pass and Kelly sizing
            is non-zero, otherwise ``None``.
        """
        try:
            return await self._evaluate_trade_inner(trade, leader_address)
        except Exception:
            logger.exception(
                "Error evaluating trade %s from %s",
                trade.get("transactionHash", trade.get("id", "unknown")),
                leader_address,
            )
            return None

    async def _evaluate_trade_inner(
        self, trade: dict[str, Any], leader_address: str
    ) -> CopySignal | None:
        """Core evaluation logic, separated for clean error handling."""
        market_id = trade.get("conditionId", trade.get("market", ""))
        raw_ts = trade.get("timestamp", "")
        # Activity API returns unix int; older format uses ISO string
        if isinstance(raw_ts, (int, float)):
            match_time = datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()
        else:
            match_time = str(raw_ts)
        trade_side = trade["side"]
        leader_price = float(trade["price"])
        token_id = trade.get("asset", trade.get("asset_id", ""))

        # Map trade side to signal side; detect position reductions
        leader_is_reducing = trade_side == "SELL"
        side = "BUY_YES" if trade_side == "BUY" else "BUY_NO"

        # --- Market metadata (DB cache first, then Gamma API) ---
        market_meta = await self._get_market_metadata(market_id)
        category = market_meta.get("category", "unknown")
        neg_risk = bool(market_meta.get("neg_risk", False))
        question = market_meta.get("question", "")

        # --- Leader category stats ---
        leader_wr, leader_positions = await self._get_leader_stats(
            leader_address, category
        )

        # --- Current price and spread from CLOB ---
        # If orderbook doesn't exist, the market is resolved/expired — skip
        spread = await self._safe_get_spread(token_id)
        if spread >= 1.0:
            logger.debug("Skipping resolved/expired market %s (no orderbook)", market_id)
            return None
        midpoint = await self._safe_get_midpoint(token_id)

        # --- Price drift ---
        price_drift = abs(midpoint - leader_price) / max(leader_price, 0.01)

        # --- Slippage estimation ---
        ct = self._config.copy_trading
        estimated_slippage = await self._estimate_slippage(
            token_id, side, leader_price, ct.max_position_per_market
        )

        # --- Position count for exposure filter ---
        position_count = await self._safe_get_position_count()

        # --- Duplicate market check ---
        market_has_position = await self._check_market_position(market_id)

        # --- Trade age ---
        trade_age = self._calculate_age(match_time)

        # --- Filter pipeline ---
        filter_input = {
            "category": category,
            "leader_category_wr": leader_wr,
            "leader_category_positions": leader_positions,
            "trade_age_seconds": trade_age,
            "leader_is_reducing": leader_is_reducing,
            "price_drift": price_drift,
            "spread": spread,
            "estimated_slippage": estimated_slippage,
            "current_positions_count": position_count,
            "market_has_position": market_has_position,
        }

        result = apply_filters(filter_input, ct)
        if not result.passed:
            logger.debug(
                "Trade %s rejected: %s", trade.get("id", ""), result.reason
            )
            return None

        # --- Tier-based Kelly position sizing ---
        wallet_tier = self._wallet_tiers.get(leader_address, 4)
        tier_mults = self._config.copy_trading.tier_kelly_multipliers
        tier_mult = tier_mults.get(wallet_tier, 0.0)

        if tier_mult == 0.0:
            # Tier 4: track the trade for data but don't copy
            logger.debug(
                "Tier 4 wallet %s: recording trade but not copying",
                leader_address[:10],
            )
            return None

        risk = self._config.risk
        kelly_frac = risk.kelly_fraction * 0.7 * tier_mult
        position_size = kelly_size(
            p_true=leader_wr,
            market_price=midpoint,
            bankroll=risk.starting_bankroll,
            kelly_fraction=kelly_frac,
            max_position_pct=risk.max_position_pct,
            cash_reserve_pct=risk.cash_reserve_pct,
            min_trade_size=risk.min_trade_size,
        )
        position_size = min(position_size, ct.max_position_per_market)

        if position_size == 0:
            return None

        # --- Resolve leader username ---
        leader_username = await self._get_leader_username(leader_address)

        return CopySignal(
            market_id=market_id,
            token_id=token_id,
            question=question,
            category=category,
            side=side,
            leader_address=leader_address,
            leader_username=leader_username,
            leader_price=leader_price,
            current_price=midpoint,
            estimated_slippage=estimated_slippage,
            position_size_usd=position_size,
            leader_category_wr=leader_wr,
            kelly_fraction=kelly_frac,
            neg_risk=neg_risk,
            reasoning=(
                f"Copy {leader_username} T{wallet_tier} | {category} WR={leader_wr:.0%} "
                f"| kelly={kelly_frac:.3f} | drift={price_drift:.4f} | slip={estimated_slippage:.4f}"
            ),
        )

    async def _get_market_metadata(self, market_id: str) -> dict[str, Any]:
        """Fetch market metadata from cache or Gamma API."""
        cached = await self._db.get_cached_market(market_id)
        if cached is not None:
            return cached

        market_data = await self._api.get_market(market_id)
        tags = market_data.get("tags", [])
        category = tags[0].get("label", "unknown") if tags else "unknown"

        cache_entry = {
            "market_id": market_id,
            "condition_id": market_data.get("conditionId", market_data.get("condition_id", market_id)),
            "token_id_yes": "",
            "token_id_no": "",
            "question": market_data.get("question", ""),
            "category": category.lower(),
            "neg_risk": market_data.get("negRisk", market_data.get("neg_risk", False)),
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._db.cache_market(cache_entry)
        return cache_entry

    async def _get_leader_stats(
        self, leader_address: str, category: str
    ) -> tuple[float, int]:
        """Retrieve leader win rate and resolved positions for a category.

        Returns:
            Tuple of (win_rate, resolved_positions) defaulting to
            (0.0, 0) if not found.
        """
        row = await self._db.fetch_one(
            "SELECT category_stats FROM wallet_profiles WHERE address = ?",
            (leader_address,),
        )
        if row is None:
            return 0.0, 0

        raw = row.get("category_stats", "{}")
        stats = json.loads(raw) if isinstance(raw, str) else raw
        cat_stats = stats.get(category, {})
        return (
            float(cat_stats.get("win_rate", 0.0)),
            int(cat_stats.get("resolved_positions", 0)),
        )

    async def _safe_get_spread(self, token_id: str) -> float:
        """Fetch spread from CLOB, returning 1.0 on failure (resolved market)."""
        try:
            return await self._api.get_spread(token_id)
        except Exception:
            logger.debug("No orderbook for token %s (likely resolved)", token_id[:20])
            return 1.0

    async def _safe_get_midpoint(self, token_id: str) -> float:
        """Fetch midpoint from CLOB, returning 0.5 on failure."""
        try:
            return await self._api.get_midpoint(token_id)
        except Exception:
            logger.debug("No midpoint for token %s (likely resolved)", token_id[:20])
            return 0.5

    async def _estimate_slippage(
        self,
        token_id: str,
        side: str,
        leader_price: float,
        max_position: float,
    ) -> float:
        """Estimate fill slippage from the CLOB orderbook.

        Returns 1.0 (maximum slippage) when the book cannot fill the
        order or on any API error.
        """
        try:
            book = await self._api.get_book(token_id)
            book_side = "BUY" if "YES" in side else "SELL"
            fill_price = estimate_fill_price(book, book_side, max_position)
            if fill_price is None:
                return 1.0
            return calculate_slippage(leader_price, fill_price)
        except Exception:
            logger.exception("Failed to estimate slippage for %s", token_id)
            return 1.0

    async def _safe_get_position_count(self) -> int:
        """Fetch current position count, returning 0 on failure."""
        try:
            return await self._db.get_position_count()
        except Exception:
            logger.exception("Failed to get position count")
            return 0

    async def _check_market_position(self, market_id: str) -> bool:
        """Check whether a position already exists for this market."""
        try:
            row = await self._db.fetch_one(
                "SELECT 1 FROM positions WHERE market_id = ?",
                (market_id,),
            )
            return row is not None
        except Exception:
            logger.exception("Failed to check market position for %s", market_id)
            return False

    async def _get_leader_username(self, leader_address: str) -> str:
        """Look up the leader's username from wallet_profiles."""
        try:
            row = await self._db.fetch_one(
                "SELECT username FROM wallet_profiles WHERE address = ?",
                (leader_address,),
            )
            return row["username"] if row else leader_address
        except Exception:
            logger.exception("Failed to get username for %s", leader_address)
            return leader_address

    def _calculate_age(self, match_time: str) -> float:
        """Parse an ISO timestamp and return seconds since now.

        Returns 999 on any parse error to ensure stale-trade filtering
        rejects unparseable timestamps.
        """
        try:
            dt = datetime.fromisoformat(match_time.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - dt).total_seconds()
        except (ValueError, AttributeError):
            return 999.0
