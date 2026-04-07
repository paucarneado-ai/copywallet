"""Tests for the sliding window rate limiter."""

import asyncio
import time

import pytest

from src.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_limit() -> None:
    """Acquiring well under the limit should complete near-instantly."""
    limiter = RateLimiter(max_requests_per_minute=60)

    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"5 acquires took {elapsed:.3f}s, expected <1s"


@pytest.mark.asyncio
async def test_rate_limiter_tracks_count() -> None:
    """The requests_in_window property must reflect recorded timestamps."""
    limiter = RateLimiter(max_requests_per_minute=100)

    for _ in range(10):
        await limiter.acquire()

    assert limiter.requests_in_window >= 10, (
        f"Expected >=10 requests tracked, got {limiter.requests_in_window}"
    )


@pytest.mark.asyncio
async def test_rate_limiter_delays_at_threshold() -> None:
    """When usage exceeds warn_threshold, acquire adds a proportional delay."""
    limiter = RateLimiter(max_requests_per_minute=10, warn_threshold=0.8)

    # Manually inject 9 timestamps to push above the 80% threshold.
    # usage = 9/10 = 0.9 -> delay = 0.1 * (0.9 - 0.8) / (1.0 - 0.8) = 50ms
    now = time.monotonic()
    for _ in range(9):
        limiter._timestamps.append(now)

    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.05, (
        f"Expected >=50ms throttle delay, got {elapsed * 1000:.1f}ms"
    )
