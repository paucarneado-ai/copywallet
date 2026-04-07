"""Configuration loader for Copywallet.

Reads a YAML file, applies defaults for missing keys, and validates
constraints (trading mode, dashboard security).
"""

import logging
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Type, TypeVar

import yaml

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Default fee schedule by market category
# ---------------------------------------------------------------------------
DEFAULT_FEES: Dict[str, float] = {
    "crypto": 0.018,
    "sports": 0.0075,
    "politics": 0.01,
    "economics": 0.015,
    "geopolitics": 0.0,
    "default": 0.01,
}


# ---------------------------------------------------------------------------
# Nested configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PolymarketConfig:
    """Connection details for Polymarket APIs."""

    private_key: str = ""
    proxy_wallet: str = ""
    clob_host: str = "https://clob.polymarket.com"
    data_api_host: str = "https://data-api.polymarket.com"
    gamma_api_host: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137


@dataclass
class RiskConfig:
    """Position-sizing and loss-limit parameters."""

    starting_bankroll: int = 10000
    kelly_fraction: float = 0.25
    max_position_pct: float = 0.05
    cash_reserve_pct: float = 0.30
    max_concurrent_positions: int = 10
    daily_loss_limit_pct: float = 0.05
    min_trade_size: int = 10


@dataclass
class CopyTradingConfig:
    """Wallet-following strategy parameters."""

    enabled: bool = True
    poll_interval_seconds: int = 2
    discovery_interval_hours: int = 6
    min_win_rate: float = 0.55
    max_win_rate: float = 0.90
    min_resolved_positions: int = 50
    min_category_positions: int = 20
    min_volume: int = 100000
    min_profit_factor: float = 1.5
    categories: List[str] = field(default_factory=lambda: ["CRYPTO"])
    max_trade_age_seconds: int = 60
    max_price_drift: float = 0.05
    max_spread: float = 0.10
    max_slippage: float = 0.02
    max_position_per_market: int = 100
    max_concurrent_positions: int = 10
    max_tracked_wallets: int = 15


@dataclass
class ScannerConfig:
    """Market-scanning filters."""

    min_volume_24h: int = 5000
    min_time_to_resolution_hours: int = 1
    max_spread: float = 0.10
    categories: List[str] = field(default_factory=lambda: ["crypto"])


@dataclass
class DashboardConfig:
    """FastAPI web dashboard settings."""

    host: str = "127.0.0.1"
    port: int = 8080
    password: str = ""


@dataclass
class DatabaseConfig:
    """SQLite storage location."""

    path: str = "copywallet.db"


@dataclass
class LoggingConfig:
    """Logging verbosity and destination."""

    level: str = "INFO"
    file: str = "copywallet.log"


@dataclass
class GraduationConfig:
    """Criteria that must be met before switching from paper to live."""

    min_trades: int = 200
    min_days: int = 14
    min_win_rate: float = 0.52
    min_profit_factor: float = 1.2
    max_drawdown: float = 0.15
    auto_scale_hours: int = 48


@dataclass
class ClaudeBrainConfig:
    """Claude-powered autonomous trading brain parameters.

    Controls whether the brain pipeline is active, which model to use
    for probability estimation, how often to scan for markets, and the
    minimum cooldown between evaluations of the same market.
    """

    enabled: bool = False
    model: str = "claude-sonnet-4-20250514"
    scan_interval_seconds: int = 120
    cooldown_minutes: int = 15


# ---------------------------------------------------------------------------
# Top-level Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Root configuration object aggregating all sections."""

    mode: str = "paper"
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    copy_trading: CopyTradingConfig = field(default_factory=CopyTradingConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    graduation: GraduationConfig = field(default_factory=GraduationConfig)
    claude_brain: ClaudeBrainConfig = field(default_factory=ClaudeBrainConfig)
    fees: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEES))
    telegram_bot_token: str = ""
    telegram_user_id: str = ""
    anthropic_api_key: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_dataclass(cls: Type[T], data: Dict[str, Any]) -> T:
    """Instantiate *cls* using only the keys that match its fields.

    Extra keys in *data* are silently ignored so that forward-compatible
    YAML files do not break older code.
    """
    valid_keys = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in valid_keys}
    return cls(**filtered)


# Mapping from Config field name to its nested dataclass type.
_NESTED_SECTIONS: Dict[str, Type[Any]] = {
    "polymarket": PolymarketConfig,
    "risk": RiskConfig,
    "copy_trading": CopyTradingConfig,
    "scanner": ScannerConfig,
    "dashboard": DashboardConfig,
    "database": DatabaseConfig,
    "logging": LoggingConfig,
    "graduation": GraduationConfig,
    "claude_brain": ClaudeBrainConfig,
}


def _validate(cfg: Config) -> None:
    """Raise ``ValueError`` when configuration invariants are violated."""
    if cfg.mode not in ("paper", "live"):
        raise ValueError(
            f"mode must be 'paper' or 'live', got '{cfg.mode}'"
        )

    if cfg.dashboard.host != "127.0.0.1" and not cfg.dashboard.password:
        raise ValueError(
            "dashboard.password is required when dashboard.host is not "
            "'127.0.0.1' (remote access enabled)"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(path: str) -> Config:
    """Read *path* as YAML and return a validated :class:`Config`.

    Missing sections and keys are filled with sensible defaults.
    Raises ``ValueError`` for invalid mode or insecure dashboard exposure.
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    # Build nested section dataclasses, falling back to defaults.
    nested_kwargs: Dict[str, Any] = {}
    for section_name, section_cls in _NESTED_SECTIONS.items():
        section_data = raw.pop(section_name, None)
        if isinstance(section_data, dict):
            nested_kwargs[section_name] = _build_dataclass(
                section_cls, section_data
            )
        # else: leave to Config's default_factory

    # Merge fees with defaults so that unspecified categories keep their rates.
    fees_data = raw.pop("fees", None)
    if isinstance(fees_data, dict):
        merged_fees = dict(DEFAULT_FEES)
        merged_fees.update(fees_data)
        nested_kwargs["fees"] = merged_fees

    # Top-level scalar fields.
    top_level_fields = {f.name for f in fields(Config)} - set(_NESTED_SECTIONS) - {"fees"}
    for key in list(raw):
        if key in top_level_fields:
            nested_kwargs[key] = raw.pop(key)

    cfg = _build_dataclass(Config, nested_kwargs)
    _validate(cfg)

    logger.info("Configuration loaded from %s (mode=%s)", path, cfg.mode)
    return cfg
