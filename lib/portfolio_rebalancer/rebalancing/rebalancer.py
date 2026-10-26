from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from functools import partial
from typing import Iterable, Mapping

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.market.context import MarketContext, MarketSnapshot
from portfolio_rebalancer.money import ONE, ZERO
from portfolio_rebalancer.rebalancing.bands import Band
from portfolio_rebalancer.rebalancing.costs import CostModel
from portfolio_rebalancer.rebalancing.enums import Side, TargetMode
from portfolio_rebalancer.rebalancing.orders import Order, SkippedOrder
from portfolio_rebalancer.rebalancing.plan import RebalancePlan
from portfolio_rebalancer.rebalancing.policies import RebalancePolicy
from portfolio_rebalancer.rebalancing.snapshot import PortfolioSnapshot
from portfolio_rebalancer.rebalancing.triggers import TriggerContext, TriggerResult

_FRACTIONAL_STEP = Decimal("0.000001")
_NOISE = Decimal("0.005")


@dataclass(frozen=True)
class _Holdings:
    """Quantities and cash at fixed prices, so orders can be simulated without touching the ledger."""

    quantities: Mapping[str, Decimal]
    cash: Decimal
    prices: Mapping[str, Decimal]
    sectors: Mapping[str, Sector]

    @classmethod
    def of(cls, portfolio: PortfolioSnapshot) -> _Holdings:
        symbols = sorted(set(portfolio.quantities) | portfolio.allocation.symbols)
        return cls(
            quantities={s: portfolio.quantities.get(s, ZERO) for s in symbols},
            cash=portfolio.cash,
            prices={s: portfolio.stocks[s].price_at(portfolio.as_of) for s in symbols},
            sectors={s: portfolio.stocks[s].sector for s in symbols},
        )

    @property
    def symbols(self) -> list[str]:
        return list(self.quantities)

    def value(self, symbol: str) -> Decimal:
        return self.quantities[symbol] * self.prices[symbol]

    @property
    def total(self) -> Decimal:
        return sum((self.value(s) for s in self.quantities), ZERO) + self.cash

    def weights(self) -> dict[str, Decimal]:
        total = self.total
        return {s: self.value(s) / total if total else ZERO for s in self.symbols}

    def sector_weights(self) -> dict[Sector, Decimal]:
        totals: dict[Sector, Decimal] = {}
        for symbol, weight in self.weights().items():
            totals[self.sectors[symbol]] = totals.get(self.sectors[symbol], ZERO) + weight
        return totals

    def after(self, orders: Iterable[Order]) -> _Holdings:
        quantities, cash = dict(self.quantities), self.cash
        for o in orders:
            quantities[o.symbol] += o.quantity if o.side is Side.BUY else -o.quantity
            cash += o.cash_delta()
        return replace(self, quantities=quantities, cash=cash)


class Rebalancer:
    """Stateless: (portfolio snapshot, policy, market) -> RebalancePlan. It never trades.

    1. A target not executed yet always triggers; a regime change returns an
       empty plan that asks for re-optimization.
    2. Cash flows first: a buy-only plan wins if it brings every position back in band.
    3. Otherwise trade to the target or to the nearest band edge (a new target always in full).
    4. Sells fund buys; buys are fitted to the cash left after fees and the cash buffer.
    """

    def plan(self, portfolio: PortfolioSnapshot, policy: RebalancePolicy, market: MarketContext | None = None) -> RebalancePlan:
        holdings = _Holdings.of(portfolio)
        targets = {s: portfolio.allocation.weight(s) for s in holdings.symbols}
        market_snapshot = market.as_of(portfolio.as_of) if market else None
        finish = partial(self._finish, portfolio, holdings, targets, market_snapshot)

        if holdings.total <= 0:
            return finish(TriggerResult(False, ("portfolio has no value to rebalance",)))
        result = self._evaluate_trigger(portfolio, policy, holdings, market_snapshot)
        if not result.triggered:
            return finish(result, notes=("all positions within tolerance",))
        if result.requires_reoptimization:
            return finish(result)

        if policy.use_cash_flows_first and not result.breached_sectors and not portfolio.allocation_pending:
            orders, skipped = self._cash_flow_orders(portfolio, holdings, targets, policy)
            if orders and self._within_bands(holdings.after(orders), targets, policy.band):
                return finish(result, orders, skipped, ("resolved by investing available cash, no sales needed",))

        mode = TargetMode.TO_TARGET if portfolio.allocation_pending else policy.target_mode
        forced = {s for s in holdings.symbols if holdings.sectors[s] in result.breached_sectors}
        desired = self._desired_weights(holdings, targets, portfolio.allocation.cash_weight, policy.band, mode, forced)
        orders, skipped = self._orders(
            holdings, desired, targets, policy, portfolio.cost_model, toward_target=mode is TargetMode.TO_BAND_EDGE
        )
        return finish(result, orders, skipped)

    @staticmethod
    def _evaluate_trigger(
        portfolio: PortfolioSnapshot,
        policy: RebalancePolicy,
        holdings: _Holdings,
        market: MarketSnapshot | None,
    ) -> TriggerResult:
        ctx = TriggerContext(
            as_of=portfolio.as_of,
            weights=holdings.weights(),
            allocation=portfolio.allocation,
            sector_weights=holdings.sector_weights(),
            last_rebalanced_at=portfolio.last_rebalanced_at,
            market=market,
        )
        result = policy.trigger.evaluate(ctx)
        if not portfolio.allocation_pending:
            return result
        return replace(result, triggered=True, reasons=result.reasons + ("new target allocation not executed yet: trading to target",))

    # Desired weights

    def _cash_flow_orders(
        self,
        portfolio: PortfolioSnapshot,
        holdings: _Holdings,
        targets: Mapping[str, Decimal],
        policy: RebalancePolicy,
    ) -> tuple[list[Order], list[SkippedOrder]]:
        """Buy-only: spread investable cash over under-weights, pro rata to their shortfall."""
        total = holdings.total
        investable = holdings.cash - (portfolio.allocation.cash_weight + policy.cash_buffer) * total
        shortfalls = {s: targets[s] * total - holdings.value(s) for s in holdings.symbols}
        shortfalls = {s: gap for s, gap in shortfalls.items() if gap > 0}
        missing = sum(shortfalls.values(), ZERO)
        if investable <= 0 or missing <= 0:
            return [], []
        invest = min(investable, missing)
        desired = {s: (holdings.value(s) + shortfalls.get(s, ZERO) / missing * invest) / total for s in holdings.symbols}
        return self._orders(holdings, desired, targets, policy, portfolio.cost_model, toward_target=True)

    @staticmethod
    def _desired_weights(
        holdings: _Holdings,
        targets: Mapping[str, Decimal],
        cash_weight: Decimal,
        band: Band,
        mode: TargetMode,
        forced_to_target: set[str],
    ) -> dict[str, Decimal]:
        if mode is TargetMode.TO_TARGET:
            return dict(targets)

        weights = holdings.weights()
        desired, in_band = {}, []
        for s in holdings.symbols:
            low, high = band.bounds(targets[s])
            if s in forced_to_target:
                desired[s] = targets[s]
            elif weights[s] < low:
                desired[s] = low
            elif weights[s] > high:
                desired[s] = high
            else:
                desired[s] = weights[s]
                in_band.append(s)

        # Trimming proceeds would otherwise pile up as cash: hand them to in-band under-weights.
        spare = ONE - cash_weight - sum(desired.values(), ZERO)
        shortfalls = {s: targets[s] - desired[s] for s in in_band if targets[s] > desired[s]}
        missing = sum(shortfalls.values(), ZERO)
        if spare > 0 and missing > 0:
            give = min(spare, missing)
            for s, gap in shortfalls.items():
                desired[s] += gap / missing * give
        return desired

    @staticmethod
    def _within_bands(holdings: _Holdings, targets: Mapping[str, Decimal], band: Band) -> bool:
        weights = holdings.weights()
        for s in holdings.symbols:
            low, high = band.bounds(targets[s])
            if not low <= weights[s] <= high:
                return False
        return True

    # Orders

    def _orders(
        self,
        holdings: _Holdings,
        desired: Mapping[str, Decimal],
        targets: Mapping[str, Decimal],
        policy: RebalancePolicy,
        cost_model: CostModel,
        toward_target: bool,
    ) -> tuple[list[Order], list[SkippedOrder]]:
        """Quantities round down when trading to the target (never overspend) and toward the
        target when trading to a band edge (rounding down would stop a fraction of a share
        outside the band, still out of tolerance)."""
        total = holdings.total
        sells, buys, skipped = [], [], []
        for s in holdings.symbols:
            held, price = holdings.quantities[s], holdings.prices[s]
            liquidation = targets[s] == 0 and desired[s] == 0 and held > 0
            if liquidation:
                side, quantity = Side.SELL, held
            else:
                delta = desired[s] * total - holdings.value(s)
                if abs(delta) < _NOISE:
                    continue
                side = Side.BUY if delta > 0 else Side.SELL
                quantity = _round(abs(delta) / price, policy.fractional_shares, up=toward_target)
                if side is Side.SELL:
                    quantity = min(quantity, held)
                if quantity == 0:
                    continue
            order = Order(s, side, quantity, price, cost_model.estimate(s, side, quantity, price), liquidation)
            reason = None if liquidation else _skip_reason(order, policy)
            if reason:
                skipped.append(SkippedOrder(s, side, order.value, reason))
            else:
                (sells if side is Side.SELL else buys).append(order)

        available = holdings.cash + sum((o.cash_delta() for o in sells), ZERO) - policy.cash_buffer * total
        fitted = self._fit_to_cash(buys, available, policy, cost_model)
        fitted_quantities = {o.symbol: o.quantity for o in fitted}
        for o in buys:
            missing = o.quantity - fitted_quantities.get(o.symbol, ZERO)
            if missing > 0:
                skipped.append(SkippedOrder(o.symbol, o.side, missing * o.price, "reduced: not enough cash"))
        return sells + fitted, skipped

    def _fit_to_cash(self, buys: list[Order], available: Decimal, policy: RebalancePolicy, cost_model: CostModel) -> list[Order]:
        if _cost(buys) <= available:
            return buys
        if available <= 0:
            return []
        fitted = self._scale_down(buys, available, policy, cost_model)
        if policy.fractional_shares:
            return fitted
        return self._top_up(fitted, buys, available, cost_model)

    @staticmethod
    def _scale_down(buys: list[Order], available: Decimal, policy: RebalancePolicy, cost_model: CostModel) -> list[Order]:
        """Pro-rata scaling; repeated because fixed fees can make the first pass insufficient."""
        factor = available / _cost(buys)
        while True:
            scaled = [_resize(o, _round(o.quantity * factor, policy.fractional_shares, up=False), cost_model) for o in buys]
            scaled = [o for o in scaled if o.quantity > 0]
            if _cost(scaled) <= available:
                return scaled
            factor *= Decimal("0.99")

    @staticmethod
    def _top_up(fitted: list[Order], wanted: list[Order], available: Decimal, cost_model: CostModel) -> list[Order]:
        """Spend the cash left by rounding, one share at a time, on the buy furthest from what it wanted."""
        quantities = {o.symbol: o.quantity for o in fitted}

        def build() -> list[Order]:
            return [_resize(o, quantities[o.symbol], cost_model) for o in wanted if quantities.get(o.symbol, ZERO) > 0]

        def gap(o: Order) -> Decimal:
            return (o.quantity - quantities.get(o.symbol, ZERO)) * o.price

        added = True
        while added:
            added = False
            for o in sorted((o for o in wanted if gap(o) > 0), key=gap, reverse=True):
                quantities[o.symbol] = quantities.get(o.symbol, ZERO) + 1
                if _cost(build()) <= available:
                    added = True
                    break
                quantities[o.symbol] -= 1
        return build()

    @staticmethod
    def _finish(
        portfolio: PortfolioSnapshot,
        holdings: _Holdings,
        targets: Mapping[str, Decimal],
        market: MarketSnapshot | None,
        result: TriggerResult,
        orders: Iterable[Order] = (),
        skipped: Iterable[SkippedOrder] = (),
        notes: tuple[str, ...] = (),
    ) -> RebalancePlan:
        orders = tuple(orders)
        after = holdings.after(orders)
        return RebalancePlan(
            as_of=portfolio.as_of,
            triggered=result.triggered,
            reasons=result.reasons + notes,
            orders=orders,
            skipped=tuple(skipped),
            requires_reoptimization=result.requires_reoptimization,
            target_weights=dict(targets),
            weights_before=holdings.weights(),
            weights_after=after.weights(),
            sector_weights_before=holdings.sector_weights(),
            sector_weights_after=after.sector_weights(),
            cash_before=holdings.cash,
            cash_after=after.cash,
            total_value_before=holdings.total,
            estimated_costs=sum((o.estimated_fee for o in orders), ZERO),
            risk_free_rate=market.risk_free_rate if market else None,
            ledger_size=portfolio.ledger_size,
            allocation_version=portfolio.allocation_version,
        )


def _round(quantity: Decimal, fractional: bool, up: bool) -> Decimal:
    step = _FRACTIONAL_STEP if fractional else ONE
    return quantity.quantize(step, rounding=ROUND_UP if up else ROUND_DOWN)


def _resize(order: Order, quantity: Decimal, cost_model: CostModel) -> Order:
    return replace(order, quantity=quantity, estimated_fee=cost_model.estimate(order.symbol, order.side, quantity, order.price))


def _cost(orders: Iterable[Order]) -> Decimal:
    return sum((o.value + o.estimated_fee for o in orders), ZERO)


def _skip_reason(order: Order, policy: RebalancePolicy) -> str | None:
    if order.value < policy.min_trade_value:
        return "below minimum trade value"
    if order.estimated_fee > policy.max_cost_ratio * order.value:
        return f"fee {order.estimated_fee} exceeds {policy.max_cost_ratio:.2%} of value"
    return None
