"""Abstract base class defining the executor interface.

Every executor -- paper, live, or replay -- must implement these four
methods so the rest of the system can swap execution backends without
changing calling code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.copytrade.models import CopySignal
from src.executor.models import OrderResult, Position


class BaseExecutor(ABC):
    """Contract that all order executors must satisfy."""

    @abstractmethod
    async def execute(self, signal: CopySignal) -> OrderResult:
        """Place an order derived from *signal* and return the result."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Return all currently open positions."""
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        """Return the available cash balance."""
        ...

    @abstractmethod
    async def close_position(
        self, market_id: str, side: str, signal_source: str
    ) -> OrderResult | None:
        """Close an existing position and return the closing order, or ``None``."""
        ...
