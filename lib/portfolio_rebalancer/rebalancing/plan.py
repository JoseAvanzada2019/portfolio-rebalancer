from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.money import ZERO
from portfolio_rebalancer.rebalancing.enums import Side
from portfolio_rebalancer.rebalancing.orders import Order, SkippedOrder


@dataclass(frozen=True)
class RebalancePlan:
    as_of: date
    triggered: bool
    reasons: tuple[str, ...]
    orders: tuple[Order, ...]
    skipped: tuple[SkippedOrder, ...]
    requires_reoptimization: bool
    target_weights: Mapping[str, Decimal]
    weights_before: Mapping[str, Decimal]
    weights_after: Mapping[str, Decimal]
    sector_weights_before: Mapping[Sector, Decimal]
    sector_weights_after: Mapping[Sector, Decimal]
    cash_before: Decimal
    cash_after: Decimal
    total_value_before: Decimal
    estimated_costs: Decimal
    risk_free_rate: Decimal | None
    ledger_size: int
    allocation_version: int

    @property
    def sells(self) -> tuple[Order, ...]:
        return tuple(o for o in self.orders if o.side is Side.SELL)

    @property
    def buys(self) -> tuple[Order, ...]:
        return tuple(o for o in self.orders if o.side is Side.BUY)

    @property
    def is_empty(self) -> bool:
        return not self.orders

    def summary(self) -> str:
        lines = [f"Rebalance plan as of {self.as_of} (portfolio value {self.total_value_before:,.2f})"]
        if self.risk_free_rate is not None:
            lines.append(f"Risk-free rate: {self.risk_free_rate:.2%}")
        lines += [f"  - {r}" for r in self.reasons]
        if self.requires_reoptimization:
            lines.append("  ! target allocation is stale: build a new Allocation before trading")
        if self.orders:
            lines.append("Orders:")
            lines += [
                f"  {o.side.value:<4} {o.symbol:<6} {o.quantity:>12,.4f} @ {o.price:>10,.2f}"
                f" = {o.value:>12,.2f}  (fee {o.estimated_fee:,.2f})"
                for o in self.orders
            ]
        lines += [f"  skipped {s.side.value} {s.symbol} {s.value:,.2f}: {s.reason}" for s in self.skipped]
        lines.append(f"{'Symbol':<8}{'Target':>9}{'Before':>9}{'After':>9}")
        lines += [
            f"{symbol:<8}{self.target_weights.get(symbol, ZERO):>9.2%}"
            f"{self.weights_before[symbol]:>9.2%}{self.weights_after[symbol]:>9.2%}"
            for symbol in sorted(self.weights_before)
        ]
        lines.append(f"Cash: {self.cash_before:,.2f} -> {self.cash_after:,.2f}; estimated costs {self.estimated_costs:,.2f}")
        return "\n".join(lines)
