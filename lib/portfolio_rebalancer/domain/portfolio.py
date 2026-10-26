from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Iterable, Mapping
from uuid import uuid4

from portfolio_rebalancer.domain.allocation import Allocation
from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.domain.exceptions import StalePlanError
from portfolio_rebalancer.domain.ledger import Ledger
from portfolio_rebalancer.domain.position import Position
from portfolio_rebalancer.domain.stock import Stock
from portfolio_rebalancer.domain.transaction import Transaction
from portfolio_rebalancer.market.context import MarketContext
from portfolio_rebalancer.money import ZERO, to_decimal
from portfolio_rebalancer.rebalancing.costs import CostModel, ZeroCost
from portfolio_rebalancer.rebalancing.enums import Side
from portfolio_rebalancer.rebalancing.plan import RebalancePlan
from portfolio_rebalancer.rebalancing.policies import RebalancePolicy, default_policy
from portfolio_rebalancer.rebalancing.rebalancer import Rebalancer
from portfolio_rebalancer.rebalancing.snapshot import PortfolioSnapshot


class Portfolio:
    """A universe of stocks, a ledger of movements and a target allocation (with its history).

    ``risk_target`` is the annualized volatility the investor accepts (0.15 == 15%).
    """

    def __init__(
        self,
        name: str,
        allocation: Allocation,
        stocks: Iterable[Stock] = (),
        cost_model: CostModel | None = None,
        risk_target: Decimal | float | None = None,
        base_currency: str = "USD",
        portfolio_id: str | None = None,
    ) -> None:
        self.id = portfolio_id or uuid4().hex
        self.name = name
        self.base_currency = base_currency
        self.cost_model: CostModel = cost_model or ZeroCost()
        self.risk_target = to_decimal(risk_target) if risk_target is not None else None
        self.last_rebalanced_at: date | None = None
        self._stocks: dict[str, Stock] = {}
        self._ledger = Ledger()
        self._allocation_history: list[Allocation] = []
        self._allocation_pending = False
        for stock in stocks:
            self.add_stock(stock)
        self.set_allocation(allocation)

    # Universe and target

    @property
    def stocks(self) -> Mapping[str, Stock]:
        return MappingProxyType(self._stocks)

    def stock(self, symbol: str) -> Stock:
        try:
            return self._stocks[symbol]
        except KeyError:
            raise KeyError(f"{symbol} is not part of this portfolio's universe") from None

    def add_stock(self, stock: Stock) -> None:
        if stock.currency != self.base_currency:
            raise ValueError(f"{stock.symbol} is in {stock.currency}; FX is out of scope (base {self.base_currency})")
        self._stocks[stock.symbol] = stock

    @property
    def allocation(self) -> Allocation:
        return self._allocation_history[-1]

    @property
    def allocation_history(self) -> tuple[Allocation, ...]:
        return tuple(self._allocation_history)

    @property
    def allocation_pending(self) -> bool:
        """True until a plan for the current target is applied; a new target is then traded to in full."""
        return self._allocation_pending

    def set_allocation(self, allocation: Allocation, new_stocks: Iterable[Stock] = ()) -> None:
        for stock in new_stocks:
            self.add_stock(stock)
        unknown = allocation.symbols - set(self._stocks)
        if unknown:
            raise ValueError(f"allocation references unknown symbols: {sorted(unknown)}")
        self._allocation_history.append(allocation)
        self._allocation_pending = True

    # Ledger

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        return self._ledger.transactions

    def record(self, transaction: Transaction) -> None:
        if transaction.symbol is not None:
            self.stock(transaction.symbol)
        self._ledger.record(transaction)

    def deposit(self, amount: Decimal | float | int | str, timestamp: datetime | None = None) -> None:
        self.record(Transaction.deposit(amount, timestamp))

    def withdraw(self, amount: Decimal | float | int | str, timestamp: datetime | None = None) -> None:
        self.record(Transaction.withdrawal(amount, timestamp))

    # Derived state

    def positions(self, as_of: date | None = None) -> dict[str, Position]:
        return dict(self._ledger.state(as_of).positions)

    def cash(self, as_of: date | None = None) -> Decimal:
        return self._ledger.state(as_of).cash

    def market_values(self, as_of: date | None = None) -> dict[str, Decimal]:
        return {s: p.market_value(self._price(s, as_of)) for s, p in self.positions(as_of).items()}

    def total_value(self, as_of: date | None = None) -> Decimal:
        return sum(self.market_values(as_of).values(), ZERO) + self.cash(as_of)

    def current_weights(self, as_of: date | None = None) -> dict[str, Decimal]:
        """Weights over total value (cash included) for every symbol held or targeted."""
        values = self.market_values(as_of)
        total = sum(values.values(), ZERO) + self.cash(as_of)
        symbols = sorted(set(values) | self.allocation.symbols)
        return {s: values.get(s, ZERO) / total if total else ZERO for s in symbols}

    def sector_weights(self, as_of: date | None = None) -> dict[Sector, Decimal]:
        totals: dict[Sector, Decimal] = {}
        for symbol, weight in self.current_weights(as_of).items():
            sector = self.stock(symbol).sector
            totals[sector] = totals.get(sector, ZERO) + weight
        return totals

    def drift(self, as_of: date | None = None) -> dict[str, Decimal]:
        return {s: w - self.allocation.weight(s) for s, w in self.current_weights(as_of).items()}

    def _price(self, symbol: str, as_of: date | None) -> Decimal:
        stock = self.stock(symbol)
        return stock.current_price() if as_of is None else stock.price_at(as_of)

    # Rebalancing

    def snapshot(self, as_of: date | None = None) -> PortfolioSnapshot:
        as_of = as_of or date.today()
        state = self._ledger.state(as_of)
        return PortfolioSnapshot(
            as_of=as_of,
            allocation=self.allocation,
            quantities={s: p.quantity for s, p in state.positions.items()},
            cash=state.cash,
            stocks=self.stocks,
            cost_model=self.cost_model,
            last_rebalanced_at=self.last_rebalanced_at,
            allocation_pending=self._allocation_pending,
            ledger_size=len(self._ledger),
            allocation_version=len(self._allocation_history),
        )

    def rebalance(
        self,
        policy: RebalancePolicy | None = None,
        as_of: date | None = None,
        market: MarketContext | None = None,
    ) -> RebalancePlan:
        """Which stocks to sell and buy to get back to the allocation. Does not trade."""
        return Rebalancer().plan(self.snapshot(as_of), policy or default_policy(), market)

    def apply(self, plan: RebalancePlan, timestamp: datetime | None = None) -> None:
        """Record the plan's orders as filled at the planned price and fee (end of ``as_of`` by default)."""
        if plan.ledger_size != len(self._ledger):
            raise StalePlanError("the ledger changed after this plan was computed; plan again")
        if plan.allocation_version != len(self._allocation_history):
            raise StalePlanError("the target allocation changed after this plan was computed; plan again")
        timestamp = timestamp or datetime.combine(plan.as_of, time.max, tzinfo=timezone.utc)
        fills = [
            (Transaction.sell if o.side is Side.SELL else Transaction.buy)(
                o.symbol, o.quantity, o.price, o.estimated_fee, timestamp, "rebalance"
            )
            for o in plan.orders
        ]
        self._ledger.record(*fills)
        if fills:
            self.last_rebalanced_at = plan.as_of
            self._allocation_pending = False
