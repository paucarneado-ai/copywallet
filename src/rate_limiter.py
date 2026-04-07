"""Sliding-window rate limiter for Polymarket API calls.

Polymarket enforces 100 reads/min on Data and Gamma endpoints.
This limiter defaults to 90 RPM (10 % safety margin) and begins
proportional throttling once usage crosses a configurable warn
threshold (default 80 %).
"""

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

_WINDOW_SECONDS: float = 60.0


class RateLimiter:
    """Async-safe sliding-window rate limiter.

    Args:
        max_requests_per_minute: hard ceiling within any 60-second window.
            Defaults to 90 (leaves a 10 % buffer below Polymarket's 100).
        warn_threshold: fraction (0-1) of capacity at which proportional
            throttle delays start.  Defaults to 0.8 (80 %).
    """

    def __init__(
        self,
        max_requests_per_minute: int = 90,
        warn_threshold: float = 0.8,
    ) -> None:
        self._max_rpm: int = max_requests_per_minute
        self._warn_threshold: float = warn_threshold
        self._timestamps: deque[float] = deque()
        self._lock: asyncio.Lock = asyncio.Lock()

    def _prune(self) -> None:
        """Remove timestamps older than the 60-second window."""
        cutoff = time.monotonic() - _WINDOW_SECONDS
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    @property
    def requests_in_window(self) -> int:
        """Current request count after pruning stale entries."""
        self._prune()
        return len(self._timestamps)

    async def acquire(self) -> None:
        """Wait (if necessary) until a request slot is available, then record it.

        Behaviour at different load levels:

        * **Below warn_threshold** -- returns immediately.
        * **Between warn_threshold and 100 %** -- adds a small proportional
          delay (0-100 ms) to spread requests and avoid bursts.
        * **At 100 % capacity** -- sleeps until the oldest timestamp exits the
          window, then retries.
        """
        async with self._lock:
            while True:
                self._prune()
                current_count = len(self._timestamps)

                # --- at capacity: wait for the oldest slot to expire ----------
                if current_count >= self._max_rpm:
                    oldest = self._timestamps[0]
                    wait = _WINDOW_SECONDS - (time.monotonic() - oldest)
                    if wait > 0:
                        logger.warning(
                            "Rate limit reached (%d/%d). "
                            "Sleeping %.2fs until slot frees.",
                            current_count,
                            self._max_rpm,
                            wait,
                        )
                        await asyncio.sleep(wait)
                    continue  # re-prune and re-check after waking

                # --- above warn threshold: proportional throttle delay --------
                usage = current_count / self._max_rpm
                if usage >= self._warn_threshold:
                    delay = (
                        0.1
                        * (usage - self._warn_threshold)
                        / (1.0 - self._warn_threshold)
                    )
                    logger.debug(
                        "Throttling: usage %.0f%% -> %.0fms delay",
                        usage * 100,
                        delay * 1000,
                    )
                    await asyncio.sleep(delay)

                # --- record and return ---------------------------------------
                self._timestamps.append(time.monotonic())
                return
