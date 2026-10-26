from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Iterable, Mapping

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.domain.stock import Stock
from portfolio_rebalancer.market.price_history import PriceHistory
from portfolio_rebalancer.market.risk_free import RiskFreeRate
from portfolio_rebalancer.market.sector_risk import (
    SectorRiskHistory,
    SectorRiskSnapshot,
    compute_sector_snapshot,
)
from portfolio_rebalancer.stats import TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class MarketSnapshot:
    """The market as known on one date."""

    date: date
    risk_free_rate: Decimal
    sector_risk: Mapping[Sector, SectorRiskSnapshot] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sector_risk", MappingProxyType(dict(self.sector_risk)))


class MarketContext:
    def __init__(
        self,
        risk_free: RiskFreeRate,
        benchmark: PriceHistory,
        sector_risk: SectorRiskHistory | None = None,
    ) -> None:
        self.risk_free = risk_free
        self.benchmark = benchmark
        self.sector_risk = sector_risk or SectorRiskHistory()

    def as_of(self, on: date) -> MarketSnapshot:
        snapshots = {}
        for sector in self.sector_risk.sectors():
            latest = self.sector_risk.latest(sector, on)
            if latest is not None:
                snapshots[sector] = latest
        return MarketSnapshot(on, self.risk_free.rate_at(on), snapshots)

    def record_sector_snapshots(
        self, stocks: Iterable[Stock], on: date, window: int = TRADING_DAYS_PER_YEAR
    ) -> list[SectorRiskSnapshot]:
        stocks = list(stocks)
        recorded = []
        for sector in sorted({s.sector for s in stocks}, key=lambda s: s.value):
            snapshot = compute_sector_snapshot(sector, stocks, self.benchmark, self.risk_free, on, window)
            self.sector_risk.record(snapshot)
            recorded.append(snapshot)
        return recorded
