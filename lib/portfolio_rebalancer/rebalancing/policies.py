from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Mapping

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.money import ZERO
from portfolio_rebalancer.rebalancing.bands import Band, FiveTwentyFive
from portfolio_rebalancer.rebalancing.enums import TargetMode
from portfolio_rebalancer.rebalancing.triggers import (
    AllOf,
    AnyOf,
    CalendarTrigger,
    DriftTrigger,
    RegimeChangeTrigger,
    SectorLimitTrigger,
    TriggerPolicy,
)


@dataclass(frozen=True)
class RebalancePolicy:
    """``max_cost_ratio`` skips a trade whose fee exceeds that fraction of its value;
    ``cash_buffer`` is the fraction of portfolio value always kept in cash."""

    trigger: TriggerPolicy
    band: Band = field(default_factory=FiveTwentyFive)
    target_mode: TargetMode = TargetMode.TO_BAND_EDGE
    use_cash_flows_first: bool = True
    fractional_shares: bool = False
    min_trade_value: Decimal = ZERO
    max_cost_ratio: Decimal = Decimal("0.01")
    cash_buffer: Decimal = ZERO


def default_policy(
    band: Band | None = None,
    sector_limits: Mapping[Sector, Decimal] | None = None,
    check_every: timedelta = timedelta(days=91),
    detect_regime_change: bool = True,
    **overrides,
) -> RebalancePolicy:
    """Hybrid, the best cost/benefit in the backtest (results.md).

    Trades when a position is out of its 5/25 band (or a sector over its cap)
    and at least ``check_every`` has passed since the last rebalance; trades
    to the band edge, cash flows first. Regime changes are checked every day
    and stop trades toward a stale target.
    """
    band = band or FiveTwentyFive()
    drift: TriggerPolicy = DriftTrigger(band)
    if sector_limits:
        drift = AnyOf(drift, SectorLimitTrigger(sector_limits))
    trigger: TriggerPolicy = AllOf(CalendarTrigger(check_every), drift)
    if detect_regime_change:
        trigger = AnyOf(trigger, RegimeChangeTrigger())
    return RebalancePolicy(trigger=trigger, band=band, **overrides)


def calendar_policy(every: timedelta = timedelta(days=365), **overrides) -> RebalancePolicy:
    """Periodic rebalancing to the exact target: the baseline to compare against."""
    return RebalancePolicy(trigger=CalendarTrigger(every), target_mode=TargetMode.TO_TARGET, **overrides)
