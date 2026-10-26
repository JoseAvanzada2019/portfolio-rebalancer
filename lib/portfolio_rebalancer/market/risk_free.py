from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from portfolio_rebalancer.market.series import DatedSeries
from portfolio_rebalancer.money import BPS_PER_UNIT, to_decimal
from portfolio_rebalancer.stats import TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class RatePoint:
    date: date
    annual_rate: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "annual_rate", to_decimal(self.annual_rate))


class RiskFreeRate(DatedSeries[Decimal]):
    """Annual risk-free rate over time, as a fraction (0.043 == 4.3%), e.g. the US 3M T-Bill."""

    def __init__(self, source: str, tenor: str, points: Iterable[RatePoint] = ()) -> None:
        self.source = source
        self.tenor = tenor
        super().__init__((p.date, p.annual_rate) for p in points)

    def add(self, point: RatePoint) -> None:
        self._put(point.date, point.annual_rate)

    def rate_at(self, on: date) -> Decimal:
        return self._values[self._index_at(on)]

    def periodic(self, on: date, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> Decimal:
        """Compounded rate per period: (1 + r) ** (1 / n) - 1."""
        annual = float(self.rate_at(on))
        return to_decimal(round((1 + annual) ** (1 / periods_per_year) - 1, 12))

    def change_bps(self, since: date, until: date) -> Decimal:
        return (self.rate_at(until) - self.rate_at(since)) * BPS_PER_UNIT

    def history(self) -> tuple[RatePoint, ...]:
        return tuple(RatePoint(d, v) for d, v in zip(self._dates, self._values))
