from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from portfolio_rebalancer.rebalancing.enums import Side


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    estimated_fee: Decimal
    liquidation: bool = False

    @property
    def value(self) -> Decimal:
        return self.quantity * self.price

    def cash_delta(self) -> Decimal:
        sign = 1 if self.side is Side.SELL else -1
        return sign * self.value - self.estimated_fee


@dataclass(frozen=True)
class SkippedOrder:
    """A trade the drift asked for and the policy declined or reduced, with the reason."""

    symbol: str
    side: Side
    value: Decimal
    reason: str
