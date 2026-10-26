"""Strategies decide the target; the rebalancer only executes it.

A mean-variance optimizer (max return subject to σ ≤ target) would be one more
``AllocationStrategy``; it is left out because it is very sensitive to
expected-return estimates (see README).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Mapping, Protocol, Sequence

from portfolio_rebalancer.domain.allocation import Allocation
from portfolio_rebalancer.domain.stock import Stock
from portfolio_rebalancer.market.context import MarketSnapshot
from portfolio_rebalancer.money import ONE, ZERO, to_decimal
from portfolio_rebalancer.strategies.risk_model import RiskModel

_WEIGHT_STEP = Decimal("0.000001")


class AllocationStrategy(Protocol):
    def build(self, stocks: Sequence[Stock], market: MarketSnapshot | None, as_of: date | None = None) -> Allocation: ...


class ManualAllocation:
    def __init__(self, weights: Mapping[str, Decimal | float | str], cash_weight: Decimal = ZERO, label: str = "manual") -> None:
        self.weights = weights
        self.cash_weight = cash_weight
        self.label = label

    def build(self, stocks: Sequence[Stock], market: MarketSnapshot | None, as_of: date | None = None) -> Allocation:
        return Allocation(self.weights, self.cash_weight, self.label).with_context(market)


class InverseVolatility:
    """Weights ∝ 1/σ. With ``risk_target``, if the portfolio is more volatile than the target,
    stock weights are scaled down and the rest is held as cash (which earns the risk-free rate)."""

    def __init__(self, risk_model: RiskModel, risk_target: Decimal | float | None = None, label: str = "inverse-volatility") -> None:
        self.risk_model = risk_model
        self.risk_target = to_decimal(risk_target) if risk_target is not None else None
        self.label = label

    def build(self, stocks: Sequence[Stock], market: MarketSnapshot | None, as_of: date | None = None) -> Allocation:
        if not stocks:
            raise ValueError("no stocks to allocate")
        inverse = {s.symbol: 1 / self.risk_model.volatility(s.symbol, as_of) for s in stocks}
        weights = _normalized(inverse, ONE)
        cash_weight = ZERO
        if self.risk_target is not None:
            volatility = self.risk_model.portfolio_volatility(weights, as_of)
            if volatility > float(self.risk_target):
                invested = (self.risk_target / to_decimal(volatility)).quantize(_WEIGHT_STEP, rounding=ROUND_DOWN)
                weights = _normalized(inverse, invested)
                cash_weight = ONE - invested
        return Allocation(weights, cash_weight, self.label).with_context(market)


def _normalized(raw: Mapping[str, float], total: Decimal) -> dict[str, Decimal]:
    """Decimal weights proportional to ``raw`` that sum exactly to ``total``."""
    raw_total = sum(raw.values())
    weights = {s: (to_decimal(v / raw_total) * total).quantize(_WEIGHT_STEP, rounding=ROUND_DOWN) for s, v in raw.items()}
    largest = max(weights, key=weights.get)
    weights[largest] += total - sum(weights.values())
    return weights
