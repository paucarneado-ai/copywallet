"""Tests for copy trading data models: WalletProfile, CopySignal, OrderResult, Position."""

from datetime import datetime

from src.copytrade.models import CategoryStats, CopySignal, WalletProfile
from src.executor.models import OrderResult, Position


class TestWalletProfileCreation:
    """Verify WalletProfile with nested CategoryStats populates all fields."""

    def test_wallet_profile_creation(self) -> None:
        crypto_stats = CategoryStats(
            win_rate=0.62,
            resolved_positions=85,
            roi=0.35,
            profit_factor=2.1,
            avg_position_size=150.0,
            total_pnl=4200.0,
        )
        profile = WalletProfile(
            address="0xabc123",
            username="top_trader",
            categories={"CRYPTO": crypto_stats},
            total_pnl=4200.0,
            total_volume=120000.0,
        )

        assert profile.address == "0xabc123"
        assert profile.username == "top_trader"
        assert profile.total_pnl == 4200.0
        assert profile.total_volume == 120000.0
        assert profile.is_suspicious is False
        assert profile.last_scored is None

        cat = profile.categories["CRYPTO"]
        assert cat.win_rate == 0.62
        assert cat.resolved_positions == 85
        assert cat.roi == 0.35
        assert cat.profit_factor == 2.1
        assert cat.avg_position_size == 150.0
        assert cat.total_pnl == 4200.0


class TestCopySignalCreation:
    """Verify CopySignal captures all signal metadata for execution."""

    def test_copy_signal_creation(self) -> None:
        signal = CopySignal(
            market_id="cond_001",
            token_id="tok_yes_001",
            question="Will BTC hit 100k?",
            category="CRYPTO",
            side="BUY_YES",
            leader_address="0xleader",
            leader_username="whale_1",
            leader_price=0.52,
            current_price=0.54,
            estimated_slippage=0.01,
            position_size_usd=250.0,
            leader_category_wr=0.65,
            kelly_fraction=0.22,
            neg_risk=False,
            reasoning="High edge on BTC pump signal",
        )

        assert signal.market_id == "cond_001"
        assert signal.token_id == "tok_yes_001"
        assert signal.question == "Will BTC hit 100k?"
        assert signal.category == "CRYPTO"
        assert signal.side == "BUY_YES"
        assert signal.leader_address == "0xleader"
        assert signal.leader_username == "whale_1"
        assert signal.leader_price == 0.52
        assert signal.current_price == 0.54
        assert signal.estimated_slippage == 0.01
        assert signal.position_size_usd == 250.0
        assert signal.leader_category_wr == 0.65
        assert signal.kelly_fraction == 0.22
        assert signal.neg_risk is False
        assert signal.reasoning == "High edge on BTC pump signal"


class TestOrderResultCreation:
    """Verify OrderResult captures execution outcome with correct status."""

    def test_order_result_creation(self) -> None:
        result = OrderResult(
            order_id="ord_abc",
            market_id="cond_001",
            side="BUY_YES",
            price=0.54,
            size_usd=250.0,
            size_shares=462.96,
            fee=4.50,
            status="filled",
        )

        assert result.order_id == "ord_abc"
        assert result.market_id == "cond_001"
        assert result.side == "BUY_YES"
        assert result.price == 0.54
        assert result.size_usd == 250.0
        assert result.size_shares == 462.96
        assert result.fee == 4.50
        assert result.status == "filled"


class TestPositionCreation:
    """Verify Position defaults are applied correctly on creation."""

    def test_position_creation(self) -> None:
        pos = Position(
            market_id="cond_001",
            token_id="tok_yes_001",
            side="BUY_YES",
            entry_price=0.54,
            size_shares=462.96,
            size_usd=250.0,
            signal_source="copy_trade",
        )

        assert pos.market_id == "cond_001"
        assert pos.token_id == "tok_yes_001"
        assert pos.side == "BUY_YES"
        assert pos.entry_price == 0.54
        assert pos.size_shares == 462.96
        assert pos.size_usd == 250.0
        assert pos.signal_source == "copy_trade"
        assert pos.leader_address is None
        assert pos.current_price == 0.0
        assert pos.unrealized_pnl == 0.0
        assert isinstance(pos.opened_at, datetime)
