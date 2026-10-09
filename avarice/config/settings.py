"""Local settings. No credentials or paid integrations are required."""
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class AvariceConfig:
    virtual_capital_usd: float = 50.0
    chains: tuple[str, ...] = ("solana", "base", "robinhood")
    max_position_pct: float = 10.0
    max_concurrent_positions: int = 5
    min_position_usd: float = 1.0
    min_wallet_win_rate: float = 0.55
    min_wallet_trades: int = 20
    min_wallet_tokens: int = 3
    max_wallet_hold_hours: float = 6.0
    max_hold_hours: float = 24.0
    stop_loss_pct: float = 30.0
    take_profit_pct: float = 100.0
    max_drawdown_pct: float = 20.0
    min_liquidity_usd: float = 1000.0
    min_volume_usd: float = 100.0
    max_pool_age_hours: float = 24.0
    fee_bps: float = 100.0
    slippage_bps: float = 100.0
    gas_usd: dict = field(default_factory=lambda: {
        "solana": 0.02, "base": 0.05, "robinhood": 0.05,
        "ethereum": 2.0, "bsc": 0.10,
    })
    watched_pools_per_chain: int = 1
    watch_hours: float = 6.0
    signal_max_age_seconds: float = 120.0
    quote_max_age_seconds: float = 300.0
    http_timeout_seconds: float = 15.0
    http_min_interval_seconds: float = 6.5
    scan_budget_seconds: float = 150.0
    safety_max_age_seconds: float = 600.0
    research_enabled: bool = True
    research_horizon_minutes: float = 60.0
    research_max_label_delay_minutes: float = 30.0
    research_min_samples: int = 20
    research_min_validation_days: int = 3

    def __post_init__(self):
        errors = validate_config(self)
        if errors:
            raise ValueError("; ".join(errors))

    def to_dict(self):
        return asdict(self)


def validate_config(settings):
    errors = []
    for name, value in asdict(settings).items():
        if (name != "research_enabled" and isinstance(value, (int, float))
                and (not math.isfinite(value) or value <= 0)):
            errors.append(f"{name} must be finite and positive")
    if not 0 < settings.max_position_pct <= 10:
        errors.append("max_position_pct must be at most 10")
    if not 0 < settings.stop_loss_pct < 100:
        errors.append("stop_loss_pct must be below 100")
    if not 0 < settings.min_wallet_win_rate <= 1:
        errors.append("min_wallet_win_rate must be in (0, 1]")
    if settings.fee_bps >= 10000 or settings.slippage_bps >= 10000:
        errors.append("cost rates must be below 10000 bps")
    known = {"solana", "base", "robinhood", "ethereum", "bsc"}
    if not settings.chains or any(chain not in known for chain in settings.chains):
        errors.append("unsupported or empty chain list")
    if len(set(settings.chains)) != len(settings.chains):
        errors.append("chain list contains duplicates")
    for chain in settings.chains:
        value = settings.gas_usd.get(chain)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            errors.append(f"gas_usd.{chain} must be finite and positive")
    for name in ("max_concurrent_positions", "min_wallet_trades", "min_wallet_tokens", "watched_pools_per_chain",
                 "research_min_samples", "research_min_validation_days"):
        if type(getattr(settings, name)) is not int:
            errors.append(f"{name} must be an integer")
    if type(settings.research_enabled) is not bool:
        errors.append("research_enabled must be boolean")
    for name in ("safety_max_age_seconds", "research_horizon_minutes", "research_max_label_delay_minutes"):
        value = getattr(settings, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            errors.append(f"{name} must be finite and positive")
    if type(settings.safety_max_age_seconds) in (int, float) and settings.safety_max_age_seconds > 600:
        errors.append("safety_max_age_seconds must be at most 600")
    if settings.max_concurrent_positions > 5:
        errors.append("max_concurrent_positions must be at most 5")
    return errors


def load_config(path=None, chains=None):
    location = Path(path) if path else ROOT / "config.json"
    values = json.loads(location.read_text(encoding="utf-8")) if location.exists() else {}
    if chains:
        values["chains"] = tuple(chains)
    return AvariceConfig(**values)


config = AvariceConfig()