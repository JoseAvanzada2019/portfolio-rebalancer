from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from portfolio_rebalancer.money import CENT, ZERO, to_decimal
from portfolio_rebalancer.rebalancing.enums import Side


class CostModel(Protocol):
    def estimate(self, symbol: str, side: Side, quantity: Decimal, price: Decimal) -> Decimal: ...


@dataclass(frozen=True)
class ZeroCost:
    def estimate(self, symbol: str, side: Side, quantity: Decimal, price: Decimal) -> Decimal:
        return ZERO


@dataclass(frozen=True)
class FixedPlusPercentCost:
    """A fixed fee per order plus a percent of traded value (0.001 == 10bps, commission + spread)."""

    fixed: Decimal = ZERO
    percent: Decimal = ZERO

    def estimate(self, symbol: str, side: Side, quantity: Decimal, price: Decimal) -> Decimal:
        cost = to_decimal(self.fixed) + to_decimal(self.percent) * quantity * price
        return cost.quantize(CENT, rounding=ROUND_HALF_UP)
