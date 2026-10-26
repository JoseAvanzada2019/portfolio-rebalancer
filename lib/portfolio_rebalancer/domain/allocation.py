from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.market.context import MarketSnapshot
from portfolio_rebalancer.market.sector_risk import SectorRiskSnapshot
from portfolio_rebalancer.money import ONE, ZERO, to_decimal

_SUM_TOLERANCE = Decimal("0.000001")


@dataclass(frozen=True)
class Allocation:
    """Target weights plus strategic cash, summing to 1. Immutable: a new target is a new Allocation.

    It keeps the market context it was built with (risk-free rate, sector
    snapshots), so the decision can be explained later and detected as stale.
    """

    weights: Mapping[str, Decimal]
    cash_weight: Decimal = ZERO
    label: str = "manual"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    risk_free_used: Decimal | None = None
    sector_snapshots_used: Mapping[Sector, SectorRiskSnapshot] = field(default_factory=dict)

    def __post_init__(self) -> None:
        weights = {symbol: to_decimal(w) for symbol, w in self.weights.items()}
        cash_weight = to_decimal(self.cash_weight)
        if any(not symbol for symbol in weights):
            raise ValueError("empty symbol in allocation")
        if any(w < 0 for w in weights.values()) or not ZERO <= cash_weight <= ONE:
            raise ValueError("weights must be non-negative (no short selling)")
        total = sum(weights.values(), ZERO) + cash_weight
        if abs(total - ONE) > _SUM_TOLERANCE:
            raise ValueError(f"weights plus cash must sum to 1, got {total}")
        object.__setattr__(self, "weights", MappingProxyType(weights))
        object.__setattr__(self, "cash_weight", cash_weight)
        object.__setattr__(self, "sector_snapshots_used", MappingProxyType(dict(self.sector_snapshots_used)))

    @classmethod
    def from_percentages(cls, percentages: Mapping[str, float | int | str], **kwargs) -> Allocation:
        return cls({s: to_decimal(p) / 100 for s, p in percentages.items()}, **kwargs)

    @property
    def symbols(self) -> frozenset[str]:
        return frozenset(self.weights)

    def weight(self, symbol: str) -> Decimal:
        return self.weights.get(symbol, ZERO)

    def sector_weights(self, sector_of: Mapping[str, Sector]) -> dict[Sector, Decimal]:
        totals: dict[Sector, Decimal] = {}
        for symbol, w in self.weights.items():
            totals[sector_of[symbol]] = totals.get(sector_of[symbol], ZERO) + w
        return totals

    def with_context(self, market: MarketSnapshot | None) -> Allocation:
        if market is None:
            return self
        return replace(self, risk_free_used=market.risk_free_rate, sector_snapshots_used=dict(market.sector_risk))

    def swap(self, old: str, new: str, label: str | None = None, market: MarketSnapshot | None = None) -> Allocation:
        """Move ``old``'s weight to ``new``: the simplest switch to a better instrument."""
        if old not in self.weights:
            raise KeyError(old)
        weights = dict(self.weights)
        weights[new] = weights.get(new, ZERO) + weights.pop(old)
        return Allocation(weights, self.cash_weight, label or f"{self.label} ({old}->{new})").with_context(market)
