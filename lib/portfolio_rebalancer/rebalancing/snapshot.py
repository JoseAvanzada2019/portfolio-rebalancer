from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from portfolio_rebalancer.domain.allocation import Allocation
from portfolio_rebalancer.domain.stock import Stock
from portfolio_rebalancer.rebalancing.costs import CostModel


@dataclass(frozen=True)
class PortfolioSnapshot:
    """What the rebalancer needs from a portfolio, frozen at ``as_of``.

    ``ledger_size`` and ``allocation_version`` are stamped on the plan so the
    portfolio can refuse to apply it if either changed in the meantime.
    """

    as_of: date
    allocation: Allocation
    quantities: Mapping[str, Decimal]
    cash: Decimal
    stocks: Mapping[str, Stock]
    cost_model: CostModel
    last_rebalanced_at: date | None
    allocation_pending: bool
    ledger_size: int
    allocation_version: int
