from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.domain.stock import Stock
from portfolio_rebalancer.market.price_history import PriceHistory
from portfolio_rebalancer.market.risk_free import RiskFreeRate
from portfolio_rebalancer.stats import (
    TRADING_DAYS_PER_YEAR,
    annualized_return,
    annualized_volatility,
    mean,
    regression_beta,
)


@dataclass(frozen=True)
class SectorRiskSnapshot:
    """How a sector related to the market and to rates over a window ending on ``date``.

    Volatility and returns are annualized; ``excess_return`` is over the
    window's average risk-free rate; ``rate_sensitivity`` is the sector's
    return per +100bps move in the risk-free rate.
    """

    date: date
    sector: Sector
    beta_to_market: float
    volatility: float
    excess_return: float
    sharpe: float
    rate_sensitivity: float
    risk_free_at_snapshot: Decimal
    window: int


class SectorRiskHistory:
    """Append-only: these relations change across rate regimes, so they are recorded, not recomputed."""

    def __init__(self) -> None:
        self._by_sector: dict[Sector, list[SectorRiskSnapshot]] = {}

    def record(self, snapshot: SectorRiskSnapshot) -> None:
        series = self._by_sector.setdefault(snapshot.sector, [])
        if series and snapshot.date <= series[-1].date:
            raise ValueError(
                f"snapshots are append-only: {snapshot.sector.value} already has one on/after {snapshot.date}"
            )
        series.append(snapshot)

    def latest(self, sector: Sector, as_of: date) -> SectorRiskSnapshot | None:
        candidates = [s for s in self._by_sector.get(sector, []) if s.date <= as_of]
        return candidates[-1] if candidates else None

    def series(self, sector: Sector, metric: str) -> list[tuple[date, float]]:
        return [(s.date, getattr(s, metric)) for s in self._by_sector.get(sector, [])]

    def sectors(self) -> frozenset[Sector]:
        return frozenset(self._by_sector)


def compute_sector_snapshot(
    sector: Sector,
    stocks: Iterable[Stock],
    benchmark: PriceHistory,
    risk_free: RiskFreeRate,
    on: date,
    window: int = TRADING_DAYS_PER_YEAR,
) -> SectorRiskSnapshot:
    """Metrics of an equal-weighted basket of the sector's stocks, on the benchmark's calendar."""
    members = [s for s in stocks if s.sector is sector]
    if not members:
        raise ValueError(f"no stocks in sector {sector.value}")
    dates = benchmark.dates_until(on, window + 1)
    if len(dates) < 3:
        raise ValueError("not enough benchmark history for a snapshot")

    sector_returns, market_returns, rate_changes, rates = [], [], [], []
    for prev, curr in zip(dates, dates[1:]):
        sector_returns.append(mean([float(s.price_at(curr) / s.price_at(prev) - 1) for s in members]))
        market_returns.append(float(benchmark.at(curr).close / benchmark.at(prev).close - 1))
        rate_changes.append(float(risk_free.rate_at(curr) - risk_free.rate_at(prev)))
        rates.append(float(risk_free.rate_at(curr)))

    volatility = annualized_volatility(sector_returns)
    excess = annualized_return(sector_returns) - mean(rates)
    return SectorRiskSnapshot(
        date=on,
        sector=sector,
        beta_to_market=regression_beta(sector_returns, market_returns),
        volatility=volatility,
        excess_return=excess,
        sharpe=excess / volatility if volatility > 0 else 0.0,
        rate_sensitivity=regression_beta(sector_returns, rate_changes) * 0.01,
        risk_free_at_snapshot=risk_free.rate_at(on),
        window=len(sector_returns),
    )
