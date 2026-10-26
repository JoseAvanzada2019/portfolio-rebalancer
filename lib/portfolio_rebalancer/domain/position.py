from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Position:
    """A holding derived from the ledger. ``avg_cost`` is per share and includes buy fees."""

    symbol: str
    quantity: Decimal
    avg_cost: Decimal

    def market_value(self, price: Decimal) -> Decimal:
        return self.quantity * price

    def unrealized_pnl(self, price: Decimal) -> Decimal:
        return (price - self.avg_cost) * self.quantity
