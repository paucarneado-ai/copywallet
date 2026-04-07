"""Tests for wallet discovery pure functions: scoring and suspicion detection."""

import pytest

from src.copytrade.discovery import is_suspicious, score_wallet_trades


class TestScoreWalletTrades:
    """Verify per-category trade scoring from raw trade dicts."""

    def test_score_multi_category_trades(self) -> None:
        """3 crypto trades (2 WON, 1 LOST) and 1 politics trade (1 WON)."""
        trades = [
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.60,
                "size": 100.0,
                "status": "WON",
                "category": "CRYPTO",
            },
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.50,
                "size": 200.0,
                "status": "WON",
                "category": "CRYPTO",
            },
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.70,
                "size": 150.0,
                "status": "LOST",
                "category": "CRYPTO",
            },
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.40,
                "size": 80.0,
                "status": "WON",
                "category": "POLITICS",
            },
        ]

        result = score_wallet_trades(trades)

        assert "CRYPTO" in result
        assert "POLITICS" in result

        crypto = result["CRYPTO"]
        assert crypto["resolved_positions"] == 3
        assert crypto["win_rate"] == pytest.approx(2 / 3, abs=0.01)

        politics = result["POLITICS"]
        assert politics["resolved_positions"] == 1
        assert politics["win_rate"] == pytest.approx(1.0)

    def test_score_calculates_profit_metrics(self) -> None:
        """Gross profit, gross loss, ROI, and profit factor are computed correctly."""
        trades = [
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.60,
                "size": 100.0,
                "status": "WON",
                "category": "CRYPTO",
            },
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.70,
                "size": 100.0,
                "status": "LOST",
                "category": "CRYPTO",
            },
        ]

        result = score_wallet_trades(trades)
        crypto = result["CRYPTO"]

        # gross_profit = 100 * (1 - 0.60) = 40.0
        # gross_loss = 100 * 0.70 = 70.0
        expected_pnl = 40.0 - 70.0  # -30.0
        expected_roi = (40.0 - 70.0) / max(70.0, 1)  # -30/70
        expected_pf = 40.0 / max(70.0, 0.01)  # 40/70

        assert crypto["total_pnl"] == pytest.approx(expected_pnl, abs=0.01)
        assert crypto["roi"] == pytest.approx(expected_roi, abs=0.01)
        assert crypto["profit_factor"] == pytest.approx(expected_pf, abs=0.01)

    def test_score_calculates_avg_position_size(self) -> None:
        """Average position size is the mean of all trade sizes in a category."""
        trades = [
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.50,
                "size": 100.0,
                "status": "WON",
                "category": "CRYPTO",
            },
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.50,
                "size": 300.0,
                "status": "WON",
                "category": "CRYPTO",
            },
        ]

        result = score_wallet_trades(trades)
        assert result["CRYPTO"]["avg_position_size"] == pytest.approx(200.0)

    def test_score_empty_trades_returns_empty(self) -> None:
        """No trades produces an empty dict."""
        assert score_wallet_trades([]) == {}

    def test_score_all_wins_no_loss(self) -> None:
        """When there are only wins, gross_loss is zero and profit_factor uses guard."""
        trades = [
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.40,
                "size": 100.0,
                "status": "WON",
                "category": "CRYPTO",
            },
        ]

        result = score_wallet_trades(trades)
        crypto = result["CRYPTO"]

        assert crypto["win_rate"] == 1.0
        # gross_profit = 100 * (1 - 0.40) = 60.0, gross_loss = 0
        # profit_factor = 60.0 / max(0, 0.01) = 6000.0
        assert crypto["profit_factor"] == pytest.approx(60.0 / 0.01, abs=1.0)

    def test_score_all_losses_no_win(self) -> None:
        """When there are only losses, win_rate is zero and ROI uses guard."""
        trades = [
            {
                "outcome": "YES",
                "side": "BUY",
                "price": 0.80,
                "size": 100.0,
                "status": "LOST",
                "category": "CRYPTO",
            },
        ]

        result = score_wallet_trades(trades)
        crypto = result["CRYPTO"]

        assert crypto["win_rate"] == 0.0
        # gross_profit = 0, gross_loss = 100 * 0.80 = 80.0
        assert crypto["total_pnl"] == pytest.approx(-80.0, abs=0.01)


class TestIsSuspicious:
    """Verify detection of manipulated or stat-padded wallets."""

    def test_high_wr_few_positions_is_suspicious(self) -> None:
        """WR > 0.90 with < 100 resolved positions flags suspicion."""
        stats = {"CRYPTO": {"win_rate": 0.95, "resolved_positions": 10}}
        assert is_suspicious(stats, max_win_rate=0.90) is True

    def test_normal_wallet_is_not_suspicious(self) -> None:
        """Moderate WR with any number of positions is fine."""
        stats = {"CRYPTO": {"win_rate": 0.62, "resolved_positions": 50}}
        assert is_suspicious(stats, max_win_rate=0.90) is False

    def test_high_wr_many_positions_not_suspicious(self) -> None:
        """High WR is acceptable when backed by >= 100 resolved positions."""
        stats = {"CRYPTO": {"win_rate": 0.92, "resolved_positions": 150}}
        assert is_suspicious(stats, max_win_rate=0.90) is False

    def test_exactly_at_threshold_with_deep_history(self) -> None:
        """WR at threshold with deep history is not suspicious."""
        stats = {"CRYPTO": {"win_rate": 0.90, "resolved_positions": 150,
                            "profit_factor": 3.0, "roi": 2.0}}
        assert is_suspicious(stats, max_win_rate=0.90) is False

    def test_multiple_categories_one_suspicious(self) -> None:
        """If any single category is suspicious, the whole wallet is flagged."""
        stats = {
            "CRYPTO": {"win_rate": 0.60, "resolved_positions": 200},
            "POLITICS": {"win_rate": 0.98, "resolved_positions": 5},
        }
        assert is_suspicious(stats, max_win_rate=0.90) is True

    def test_empty_stats_is_tier4(self) -> None:
        """No category stats means tier 4 (no data = suspicious)."""
        assert is_suspicious({}, max_win_rate=0.90) is True
