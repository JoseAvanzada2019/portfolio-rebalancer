"""Triggers decide whether to act.

Drift triggers (bands, calendar, sector caps) mean "go back to the target".
``RegimeChangeTrigger`` means "the target itself is stale" and asks for
re-optimization instead of trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Protocol

from portfolio_rebalancer.domain.allocation import Allocation
from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.market.context import MarketSnapshot
from portfolio_rebalancer.money import BPS_PER_UNIT, ZERO, to_decimal
from portfolio_rebalancer.rebalancing.bands import Band, FiveTwentyFive


@dataclass(frozen=True)
class TriggerContext:
    as_of: date
    weights: Mapping[str, Decimal]
    allocation: Allocation
    sector_weights: Mapping[Sector, Decimal]
    last_rebalanced_at: date | None
    market: MarketSnapshot | None = None


@dataclass(frozen=True)
class TriggerResult:
    triggered: bool
    reasons: tuple[str, ...] = ()
    requires_reoptimization: bool = False
    breached_sectors: frozenset[Sector] = frozenset()


NOT_TRIGGERED = TriggerResult(False)


class TriggerPolicy(Protocol):
    def evaluate(self, ctx: TriggerContext) -> TriggerResult: ...


@dataclass(frozen=True)
class AlwaysTrigger:
    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        return TriggerResult(True, ("rebalance requested unconditionally",))


@dataclass(frozen=True)
class NeverTrigger:
    """Buy and hold: only a new target gets traded."""

    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        return NOT_TRIGGERED


@dataclass(frozen=True)
class DriftTrigger:
    band: Band = field(default_factory=FiveTwentyFive)

    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        reasons = []
        for symbol in sorted(ctx.weights):
            target = ctx.allocation.weight(symbol)
            low, high = self.band.bounds(target)
            weight = ctx.weights[symbol]
            if not low <= weight <= high:
                reasons.append(f"{symbol} at {weight:.2%} is outside its band {low:.2%}..{high:.2%} (target {target:.2%})")
        return TriggerResult(True, tuple(reasons)) if reasons else NOT_TRIGGERED


@dataclass(frozen=True)
class CalendarTrigger:
    every: timedelta = timedelta(days=365)

    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        if ctx.last_rebalanced_at is None:
            return TriggerResult(True, ("portfolio was never rebalanced",))
        elapsed = ctx.as_of - ctx.last_rebalanced_at
        if elapsed >= self.every:
            return TriggerResult(True, (f"{elapsed.days} days since last rebalance (every {self.every.days})",))
        return NOT_TRIGGERED


@dataclass(frozen=True)
class SectorLimitTrigger:
    """Caps per sector: catches concentration hidden behind in-band positions that move together."""

    limits: Mapping[Sector, Decimal]

    def __post_init__(self) -> None:
        object.__setattr__(self, "limits", MappingProxyType({s: to_decimal(v) for s, v in self.limits.items()}))

    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        breached, reasons = set(), []
        for sector, limit in self.limits.items():
            weight = ctx.sector_weights.get(sector, ZERO)
            if weight > limit:
                breached.add(sector)
                reasons.append(f"sector {sector.value} at {weight:.2%} exceeds its {limit:.2%} cap")
        if not breached:
            return NOT_TRIGGERED
        return TriggerResult(True, tuple(reasons), breached_sectors=frozenset(breached))


@dataclass(frozen=True)
class RegimeChangeTrigger:
    """Compares the context stamped on the Allocation with today's market snapshot.

    Volatility thresholds are absolute annualized changes; rate sensitivity is
    return per +100bps. Silent without a market snapshot.
    """

    max_risk_free_change_bps: Decimal = Decimal("100")
    max_beta_change: float = 0.30
    max_volatility_change: float = 0.10
    max_rate_sensitivity_change: float = 0.02

    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        if ctx.market is None:
            return NOT_TRIGGERED
        reasons = self._risk_free_reasons(ctx) + self._sector_reasons(ctx)
        if not reasons:
            return NOT_TRIGGERED
        return TriggerResult(True, tuple(reasons), requires_reoptimization=True)

    def _risk_free_reasons(self, ctx: TriggerContext) -> list[str]:
        used = ctx.allocation.risk_free_used
        if used is None:
            return []
        change = (ctx.market.risk_free_rate - used) * BPS_PER_UNIT
        if abs(change) < to_decimal(self.max_risk_free_change_bps):
            return []
        return [f"risk-free rate moved {change:+.0f}bps since the allocation was built"]

    def _sector_reasons(self, ctx: TriggerContext) -> list[str]:
        reasons = []
        for sector, then in ctx.allocation.sector_snapshots_used.items():
            now = ctx.market.sector_risk.get(sector)
            if now is None or now.date <= then.date:
                continue
            checks = (
                ("beta", now.beta_to_market - then.beta_to_market, self.max_beta_change),
                ("volatility", now.volatility - then.volatility, self.max_volatility_change),
                ("rate sensitivity", now.rate_sensitivity - then.rate_sensitivity, self.max_rate_sensitivity_change),
            )
            reasons += [
                f"{sector.value} {metric} changed {delta:+.3f} since the allocation was built"
                for metric, delta, limit in checks
                if abs(delta) >= limit
            ]
        return reasons


def _merge(results: list[TriggerResult]) -> TriggerResult:
    return TriggerResult(
        True,
        tuple(reason for r in results for reason in r.reasons),
        requires_reoptimization=any(r.requires_reoptimization for r in results),
        breached_sectors=frozenset().union(*(r.breached_sectors for r in results)),
    )


class AnyOf:
    def __init__(self, *triggers: TriggerPolicy) -> None:
        self.triggers = triggers

    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        fired = [r for r in (t.evaluate(ctx) for t in self.triggers) if r.triggered]
        return _merge(fired) if fired else NOT_TRIGGERED


class AllOf:
    """E.g. the hybrid "check every 90 days, trade only if out of band"."""

    def __init__(self, *triggers: TriggerPolicy) -> None:
        self.triggers = triggers

    def evaluate(self, ctx: TriggerContext) -> TriggerResult:
        results = [t.evaluate(ctx) for t in self.triggers]
        return _merge(results) if all(r.triggered for r in results) else NOT_TRIGGERED
