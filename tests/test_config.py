"""Tests for config loading, validation, and defaults."""

import textwrap
from pathlib import Path

import pytest

from src.config import load_config


class TestLoadValidConfig:
    """Verify that a well-formed YAML produces a fully populated Config."""

    def _write_yaml(self, tmp_path: Path) -> Path:
        """Write a minimal valid config YAML and return its path."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            textwrap.dedent("""\
                mode: paper
                telegram_bot_token: "tok_123"
                telegram_user_id: "uid_456"
                anthropic_api_key: "sk-ant-test"

                polymarket:
                  private_key: "0xabc"
                  proxy_wallet: "0xdef"

                risk:
                  starting_bankroll: 5000
                  kelly_fraction: 0.20

                copy_trading:
                  enabled: true
                  poll_interval_seconds: 3

                scanner:
                  min_volume_24h: 10000

                dashboard:
                  host: "127.0.0.1"
                  port: 9090

                database:
                  path: "test.db"

                logging:
                  level: "DEBUG"

                graduation:
                  min_trades: 100

                fees:
                  crypto: 0.02
            """)
        )
        return cfg_path

    def test_loads_mode(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.mode == "paper"

    def test_loads_top_level_secrets(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.telegram_bot_token == "tok_123"
        assert cfg.telegram_user_id == "uid_456"
        assert cfg.anthropic_api_key == "sk-ant-test"

    def test_loads_polymarket_section(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.polymarket.private_key == "0xabc"
        assert cfg.polymarket.proxy_wallet == "0xdef"
        assert cfg.polymarket.clob_host == "https://clob.polymarket.com"

    def test_loads_risk_overrides(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.risk.starting_bankroll == 5000
        assert cfg.risk.kelly_fraction == 0.20

    def test_loads_copy_trading_section(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.copy_trading.enabled is True
        assert cfg.copy_trading.poll_interval_seconds == 3

    def test_loads_dashboard_section(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.dashboard.host == "127.0.0.1"
        assert cfg.dashboard.port == 9090

    def test_loads_database_section(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.database.path == "test.db"

    def test_loads_logging_section(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.logging.level == "DEBUG"

    def test_loads_graduation_override(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.graduation.min_trades == 100

    def test_loads_fees_override(self, tmp_path: Path) -> None:
        cfg = load_config(str(self._write_yaml(tmp_path)))
        assert cfg.fees["crypto"] == 0.02


class TestValidation:
    """Verify that invalid configurations are rejected."""

    def test_rejects_invalid_mode(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "bad_mode.yaml"
        cfg_path.write_text("mode: simulation\n")
        with pytest.raises(ValueError, match="mode must be 'paper' or 'live'"):
            load_config(str(cfg_path))

    def test_rejects_remote_dashboard_without_password(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "remote_no_pw.yaml"
        cfg_path.write_text(
            textwrap.dedent("""\
                mode: paper
                dashboard:
                  host: "0.0.0.0"
                  password: ""
            """)
        )
        with pytest.raises(ValueError, match="password"):
            load_config(str(cfg_path))

    def test_allows_remote_dashboard_with_password(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "remote_pw.yaml"
        cfg_path.write_text(
            textwrap.dedent("""\
                mode: paper
                dashboard:
                  host: "0.0.0.0"
                  password: "s3cret"
            """)
        )
        cfg = load_config(str(cfg_path))
        assert cfg.dashboard.host == "0.0.0.0"
        assert cfg.dashboard.password == "s3cret"

    def test_allows_localhost_without_password(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "local.yaml"
        cfg_path.write_text(
            textwrap.dedent("""\
                mode: paper
                dashboard:
                  host: "127.0.0.1"
            """)
        )
        cfg = load_config(str(cfg_path))
        assert cfg.dashboard.host == "127.0.0.1"


class TestDefaults:
    """Verify that sensible defaults are applied for missing keys."""

    def test_minimal_config_uses_defaults(self, tmp_path: Path) -> None:
        """A file with only 'mode' should load with all defaults."""
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        # Top-level defaults
        assert cfg.mode == "paper"
        assert cfg.telegram_bot_token == ""
        assert cfg.telegram_user_id == ""
        assert cfg.anthropic_api_key == ""

    def test_risk_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.risk.starting_bankroll == 10000
        assert cfg.risk.kelly_fraction == 0.25
        assert cfg.risk.max_position_pct == 0.05
        assert cfg.risk.cash_reserve_pct == 0.30
        assert cfg.risk.max_concurrent_positions == 10
        assert cfg.risk.daily_loss_limit_pct == 0.05
        assert cfg.risk.min_trade_size == 10

    def test_copy_trading_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.copy_trading.enabled is True
        assert cfg.copy_trading.poll_interval_seconds == 2
        assert cfg.copy_trading.discovery_interval_hours == 6
        assert cfg.copy_trading.min_win_rate == 0.55
        assert cfg.copy_trading.max_win_rate == 0.90
        assert cfg.copy_trading.min_resolved_positions == 50
        assert cfg.copy_trading.min_category_positions == 20
        assert cfg.copy_trading.min_volume == 100000
        assert cfg.copy_trading.min_profit_factor == 1.5
        assert cfg.copy_trading.categories == ["CRYPTO"]
        assert cfg.copy_trading.max_trade_age_seconds == 60
        assert cfg.copy_trading.max_price_drift == 0.05
        assert cfg.copy_trading.max_spread == 0.10
        assert cfg.copy_trading.max_slippage == 0.02
        assert cfg.copy_trading.max_position_per_market == 100
        assert cfg.copy_trading.max_concurrent_positions == 10
        assert cfg.copy_trading.max_tracked_wallets == 15

    def test_scanner_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.scanner.min_volume_24h == 5000
        assert cfg.scanner.min_time_to_resolution_hours == 1
        assert cfg.scanner.max_spread == 0.10
        assert cfg.scanner.categories == ["crypto"]

    def test_dashboard_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.dashboard.host == "127.0.0.1"
        assert cfg.dashboard.port == 8080
        assert cfg.dashboard.password == ""

    def test_database_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.database.path == "copywallet.db"

    def test_logging_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.logging.level == "INFO"
        assert cfg.logging.file == "copywallet.log"

    def test_graduation_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.graduation.min_trades == 200
        assert cfg.graduation.min_days == 14
        assert cfg.graduation.min_win_rate == 0.52
        assert cfg.graduation.min_profit_factor == 1.2
        assert cfg.graduation.max_drawdown == 0.15
        assert cfg.graduation.auto_scale_hours == 48

    def test_fees_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.fees["crypto"] == 0.018
        assert cfg.fees["sports"] == 0.0075
        assert cfg.fees["politics"] == 0.01
        assert cfg.fees["economics"] == 0.015
        assert cfg.fees["geopolitics"] == 0.0
        assert cfg.fees["default"] == 0.01

    def test_polymarket_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "minimal.yaml"
        cfg_path.write_text("mode: paper\n")
        cfg = load_config(str(cfg_path))

        assert cfg.polymarket.private_key == ""
        assert cfg.polymarket.proxy_wallet == ""
        assert cfg.polymarket.clob_host == "https://clob.polymarket.com"
        assert cfg.polymarket.data_api_host == "https://data-api.polymarket.com"
        assert cfg.polymarket.gamma_api_host == "https://gamma-api.polymarket.com"
        assert cfg.polymarket.chain_id == 137
