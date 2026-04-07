"""Tests for market scanner models and filtering logic.

Validates MarketOpportunity and TradeSignal dataclasses, and verifies
that MarketScanner correctly filters markets by volume and spread.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from src.scanner.models import MarketOpportunity
from src.brain.models import TradeSignal
from src.config import ScannerConfig
from src.scanner.scanner import MarketScanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_iso(hours: int = 48) -> str:
    """Return an ISO-8601 timestamp *hours* into the future."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _make_event(
    condition_id: str = "cond_001",
    question: str = "Will BTC hit 100k?",
    token_yes: str = "tok_yes_001",
    token_no: str = "tok_no_001",
    volume: float = 10_000.0,
    end_date: str | None = None,
    neg_risk: bool = False,
) -> dict:
    """Build a Gamma-API-style event dict containing one market."""
    return {
        "markets": [
            {
                "conditionId": condition_id,
                "question": question,
                "tokens": [
                    {"token_id": token_yes, "outcome": "Yes"},
                    {"token_id": token_no, "outcome": "No"},
                ],
                "volume24hr": volume,
                "endDate": end_date or _future_iso(),
                "negRisk": neg_risk,
            }
        ]
    }


def _build_scanner(
    events: list[dict] | None = None,
    midpoint: float = 0.55,
    spread: float = 0.04,
    open_positions: list[dict] | None = None,
    config: ScannerConfig | None = None,
) -> MarketScanner:
    """Construct a MarketScanner with mocked API and DB dependencies."""
    api = AsyncMock()
    api.get_events = AsyncMock(return_value=events or [])
    api.get_midpoint = AsyncMock(return_value=midpoint)
    api.get_spread = AsyncMock(return_value=spread)

    db = AsyncMock()
    db.get_open_positions = AsyncMock(return_value=open_positions or [])

    scanner_config = config or ScannerConfig()
    return MarketScanner(api=api, db=db, config=scanner_config)


# ---------------------------------------------------------------------------
# Model creation tests
# ---------------------------------------------------------------------------


class TestMarketOpportunityCreation:
    """Verify MarketOpportunity populates all fields from constructor."""

    def test_market_opportunity_creation(self) -> None:
        opp = MarketOpportunity(
            market_id="cond_001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            question="Will BTC hit 100k?",
            category="crypto",
            yes_price=0.55,
            no_price=0.45,
            volume_24h=10_000.0,
            spread=0.04,
            end_date="2026-12-31T00:00:00+00:00",
            neg_risk=False,
            is_existing_position=False,
        )

        assert opp.market_id == "cond_001"
        assert opp.token_id_yes == "tok_yes_001"
        assert opp.token_id_no == "tok_no_001"
        assert opp.question == "Will BTC hit 100k?"
        assert opp.category == "crypto"
        assert opp.yes_price == 0.55
        assert opp.no_price == 0.45
        assert opp.volume_24h == 10_000.0
        assert opp.spread == 0.04
        assert opp.end_date == "2026-12-31T00:00:00+00:00"
        assert opp.neg_risk is False
        assert opp.is_existing_position is False


class TestTradeSignalCreation:
    """Verify TradeSignal populates all fields from constructor."""

    def test_trade_signal_creation(self) -> None:
        signal = TradeSignal(
            market_id="cond_002",
            token_id="tok_yes_002",
            question="Will ETH hit 10k?",
            category="crypto",
            action="BUY_YES",
            p_true=0.70,
            p_market=0.55,
            ev=0.15,
            position_size_usd=250.0,
            kelly_fraction=0.22,
            confidence="high",
            reasoning="Strong edge detected via Claude analysis",
            neg_risk=False,
        )

        assert signal.market_id == "cond_002"
        assert signal.token_id == "tok_yes_002"
        assert signal.question == "Will ETH hit 10k?"
        assert signal.category == "crypto"
        assert signal.action == "BUY_YES"
        assert signal.p_true == 0.70
        assert signal.p_market == 0.55
        assert signal.ev == 0.15
        assert signal.position_size_usd == 250.0
        assert signal.kelly_fraction == 0.22
        assert signal.confidence == "high"
        assert signal.reasoning == "Strong edge detected via Claude analysis"
        assert signal.neg_risk is False


# ---------------------------------------------------------------------------
# Scanner filtering tests
# ---------------------------------------------------------------------------


class TestScannerFiltersLowVolume:
    """Verify MarketScanner skips markets below min_volume_24h."""

    @pytest.mark.asyncio
    async def test_scanner_filters_low_volume(self) -> None:
        low_volume_event = _make_event(volume=500.0)  # default min is 5000
        scanner = _build_scanner(events=[low_volume_event])

        results = await scanner.scan()

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_scanner_passes_sufficient_volume(self) -> None:
        high_volume_event = _make_event(volume=10_000.0)
        scanner = _build_scanner(events=[high_volume_event])

        results = await scanner.scan()

        assert len(results) == 1
        assert results[0].volume_24h == 10_000.0


class TestScannerFiltersWideSpread:
    """Verify MarketScanner skips markets with spread exceeding max_spread."""

    @pytest.mark.asyncio
    async def test_scanner_filters_wide_spread(self) -> None:
        event = _make_event(volume=10_000.0)
        scanner = _build_scanner(events=[event], spread=0.15)  # default max is 0.10

        results = await scanner.scan()

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_scanner_passes_narrow_spread(self) -> None:
        event = _make_event(volume=10_000.0)
        scanner = _build_scanner(events=[event], spread=0.04)

        results = await scanner.scan()

        assert len(results) == 1
        assert results[0].spread == 0.04


class TestScannerExistingPosition:
    """Verify MarketScanner correctly flags markets with existing positions."""

    @pytest.mark.asyncio
    async def test_scanner_flags_existing_position(self) -> None:
        event = _make_event(condition_id="cond_existing")
        positions = [{"market_id": "cond_existing"}]
        scanner = _build_scanner(events=[event], open_positions=positions)

        results = await scanner.scan()

        assert len(results) == 1
        assert results[0].is_existing_position is True

    @pytest.mark.asyncio
    async def test_scanner_no_existing_position(self) -> None:
        event = _make_event(condition_id="cond_new")
        scanner = _build_scanner(events=[event], open_positions=[])

        results = await scanner.scan()

        assert len(results) == 1
        assert results[0].is_existing_position is False


class TestScannerFiltersExpiredMarket:
    """Verify MarketScanner skips markets that resolve too soon."""

    @pytest.mark.asyncio
    async def test_scanner_filters_expiring_market(self) -> None:
        # Market ending in 30 minutes (below default 1-hour threshold)
        soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        event = _make_event(end_date=soon)
        scanner = _build_scanner(events=[event])

        results = await scanner.scan()

        assert len(results) == 0
