"""Serializable paper-only domain models; UTC timestamps throughout."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class Chain(str, Enum):
    SOLANA = "solana"
    BASE = "base"
    ROBINHOOD = "robinhood"
    ETHEREUM = "ethereum"
    BSC = "bsc"


@dataclass
class Pool:
    chain: Chain
    address: str
    token_address: str
    symbol: str
    price_usd: float
    liquidity_usd: float
    volume_24h_usd: float
    created_at: str | None
    source: str = "geckoterminal"
    dex: str = "unknown"
    quote_address: str = ""
    observed_at: str = field(default_factory=utcnow)
    buys_5m: int = 0
    sells_5m: int = 0
    price_change_5m_pct: float = 0.0
    safety_status: str = "unverified"

    @property
    def key(self):
        return (self.chain.value, self.address)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "chain": Chain(data["chain"])})


@dataclass
class VirtualPosition:
    id: str
    pool: Pool
    quantity: float
    entry_cost_usd: float
    entry_price_usd: float
    entry_fee_usd: float
    entry_time: str
    wallet_followed: str
    fee_bps: float
    slippage_bps: float
    gas_usd: float
    last_price_usd: float
    last_quote_at: str
    quote_missing: bool = False
    exit_price_usd: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None
    exit_fee_usd: float = 0.0
    pnl_usd: float = 0.0

    @property
    def is_open(self):
        return self.exit_time is None

    @property
    def liquidation_value_usd(self):
        gross = self.quantity * self.last_price_usd * (1 - self.slippage_bps / 10000)
        return max(0.0, gross * (1 - self.fee_bps / 10000) - self.gas_usd)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "pool": Pool.from_dict(data["pool"])})


@dataclass
class VirtualPortfolio:
    starting_capital_usd: float = 50.0
    cash_usd: float = 50.0
    positions: list[VirtualPosition] = field(default_factory=list)
    peak_equity_usd: float = 50.0
    max_drawdown_pct: float = 0.0
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def new(cls, capital):
        return cls(starting_capital_usd=capital, cash_usd=capital, peak_equity_usd=capital)

    @property
    def open_positions(self):
        return [p for p in self.positions if p.is_open]

    @property
    def closed_positions(self):
        return [p for p in self.positions if not p.is_open]

    @property
    def equity_usd(self):
        return self.cash_usd + sum(p.liquidation_value_usd for p in self.open_positions)

    @property
    def total_pnl_usd(self):
        return self.equity_usd - self.starting_capital_usd

    @property
    def realized_pnl_usd(self):
        return sum(p.pnl_usd for p in self.closed_positions)

    def mark(self):
        self.peak_equity_usd = max(self.peak_equity_usd, self.equity_usd)
        drawdown = (self.peak_equity_usd - self.equity_usd) / self.peak_equity_usd * 100
        self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)
        self.updated_at = utcnow()

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "positions": [VirtualPosition.from_dict(p) for p in data["positions"]]})

    def summary(self):
        closed = self.closed_positions
        return {
            "mode": "paper", "starting_capital_usd": self.starting_capital_usd,
            "cash_usd": self.cash_usd, "equity_usd": self.equity_usd,
            "total_pnl_usd": self.total_pnl_usd, "realized_pnl_usd": self.realized_pnl_usd,
            "roi_pct": self.total_pnl_usd / self.starting_capital_usd * 100,
            "open_positions": len(self.open_positions), "closed_trades": len(closed),
            "win_rate": sum(p.pnl_usd > 0 for p in closed) / len(closed) if closed else None,
            "fees_paid_usd": sum(p.entry_fee_usd + p.exit_fee_usd for p in self.positions),
            "max_drawdown_pct": self.max_drawdown_pct,
            "quote_missing_positions": sum(p.quote_missing for p in self.open_positions),
        }