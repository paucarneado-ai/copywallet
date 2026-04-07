"""Paper (simulated) executor for backtesting and dry-run operation.

Simulates order fills locally using configurable fees and slippage,
persists trades and positions to the database, and tracks a virtual
cash balance -- no real money ever leaves the wallet.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from src.copytrade.models import CopySignal
from src.executor.base import BaseExecutor
from src.executor.models import OrderResult, Position
from src.state.database import Database

logger = logging.getLogger(__name__)


class PaperExecutor(BaseExecutor):
    """Executor that simulates fills against a virtual bankroll.

    Args:
        db: Database instance (must already be connected).
        starting_bankroll: Initial virtual cash balance in USD.
        fees: Mapping of category name to fee rate.  Falls back to
              the ``"default"`` key when a category is not found.
        default_slippage: Fractional slippage applied to the fill price
                          (e.g. 0.005 = 0.5 %).
    """

    def __init__(
        self,
        db: Database,
        starting_bankroll: float = 10_000,
        fees: dict[str, float] | None = None,
        default_slippage: float = 0.005,
    ) -> None:
        self._db = db
        self.cash_balance = starting_bankroll
        self._fees = fees or {"default": 0.01}
        self._default_slippage = default_slippage

    # ------------------------------------------------------------------
    # Fee calculation
    # ------------------------------------------------------------------

    def _calculate_fee(self, signal: CopySignal) -> float:
        """Return the simulated fee for *signal*.

        Formula: ``fee_rate * size_usd * p * (1 - p)`` where *p* is the
        current market price.  The quadratic term ``p * (1 - p)`` mirrors
        the Polymarket fee schedule where fees peak at 50 % prices and
        vanish near 0 or 1.
        """
        fee_rate = self._fees.get(
            signal.category, self._fees.get("default", 0.01)
        )
        p = signal.current_price
        return signal.position_size_usd * fee_rate * p * (1 - p)

    # ------------------------------------------------------------------
    # BaseExecutor interface
    # ------------------------------------------------------------------

    async def execute(self, signal: CopySignal) -> OrderResult:
        """Simulate filling an order for *signal*.

        Calculates fee and total cost, checks balance, computes fill
        price with slippage, persists the trade and position, and
        adjusts ``cash_balance``.  Returns a cancelled result when the
        bankroll is insufficient.
        """
        fee = self._calculate_fee(signal)
        total_cost = signal.position_size_usd + fee

        # Reject when the bankroll cannot cover the order
        if total_cost > self.cash_balance:
            logger.warning(
                "Insufficient balance: need %.2f but have %.2f",
                total_cost,
                self.cash_balance,
            )
            return OrderResult(
                order_id=str(uuid.uuid4()),
                market_id=signal.market_id,
                side=signal.side,
                price=signal.current_price,
                size_usd=signal.position_size_usd,
                size_shares=0.0,
                fee=fee,
                status="cancelled",
            )

        fill_price = signal.current_price * (1 + self._default_slippage)
        shares = signal.position_size_usd / fill_price
        order_id = str(uuid.uuid4())

        # Deduct cost from virtual balance
        self.cash_balance -= total_cost

        now_iso = datetime.utcnow().isoformat()

        # Persist the trade record
        await self._db.insert_trade(
            {
                "id": order_id,
                "timestamp": now_iso,
                "market_id": signal.market_id,
                "token_id": signal.token_id,
                "question": signal.question,
                "category": signal.category,
                "side": signal.side,
                "price": fill_price,
                "size_usd": signal.position_size_usd,
                "fee": fee,
                "signal_source": "copy",
                "signal_data": json.dumps(
                    {
                        "leader": signal.leader_address,
                        "leader_price": signal.leader_price,
                        "kelly": signal.kelly_fraction,
                    }
                ),
                "order_type": "GTC",
                "status": "filled",
                "leader_address": signal.leader_address,
            }
        )

        # Persist (or accumulate into) the position
        await self._db.upsert_position(
            {
                "market_id": signal.market_id,
                "token_id": signal.token_id,
                "side": signal.side,
                "entry_price": fill_price,
                "size_shares": shares,
                "size_usd": signal.position_size_usd,
                "current_price": signal.current_price,
                "unrealized_pnl": 0.0,
                "signal_source": "copy",
                "leader_address": signal.leader_address,
                "opened_at": now_iso,
            }
        )

        logger.info(
            "Paper fill: %s %s @ %.4f  shares=%.4f  fee=%.4f  balance=%.2f",
            signal.side,
            signal.market_id,
            fill_price,
            shares,
            fee,
            self.cash_balance,
        )

        return OrderResult(
            order_id=order_id,
            market_id=signal.market_id,
            side=signal.side,
            price=fill_price,
            size_usd=signal.position_size_usd,
            size_shares=shares,
            fee=fee,
            status="filled",
        )

    async def get_positions(self) -> list[Position]:
        """Return all open positions from the database as ``Position`` objects."""
        rows = await self._db.get_open_positions()
        return [
            Position(
                market_id=row["market_id"],
                token_id=row["token_id"],
                side=row["side"],
                entry_price=row["entry_price"],
                size_shares=row["size_shares"],
                size_usd=row["size_usd"],
                signal_source=row["signal_source"],
                leader_address=row.get("leader_address"),
                current_price=row.get("current_price", 0.0),
                unrealized_pnl=row.get("unrealized_pnl", 0.0),
            )
            for row in rows
        ]

    async def get_balance(self) -> float:
        """Return the current virtual cash balance."""
        return self.cash_balance

    async def close_position(
        self, market_id: str, side: str, signal_source: str
    ) -> OrderResult | None:
        """Close the position identified by its composite key.

        Adds ``size_usd + unrealized_pnl`` back to the cash balance,
        removes the position from the database, and returns a filled
        ``OrderResult``.  Returns ``None`` if no matching position exists.
        """
        rows = await self._db.get_open_positions()
        target = None
        for row in rows:
            if (
                row["market_id"] == market_id
                and row["side"] == side
                and row["signal_source"] == signal_source
            ):
                target = row
                break

        if target is None:
            return None

        recovered = target["size_usd"] + target.get("unrealized_pnl", 0.0)
        self.cash_balance += recovered

        await self._db.remove_position(market_id, side, signal_source)

        logger.info(
            "Paper close: %s %s  recovered=%.2f  balance=%.2f",
            side,
            market_id,
            recovered,
            self.cash_balance,
        )

        return OrderResult(
            order_id=str(uuid.uuid4()),
            market_id=market_id,
            side=side,
            price=target.get("current_price", target["entry_price"]),
            size_usd=target["size_usd"],
            size_shares=target["size_shares"],
            fee=0.0,
            status="filled",
        )
