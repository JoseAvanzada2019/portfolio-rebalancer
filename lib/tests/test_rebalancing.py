from datetime import timedelta
from decimal import Decimal

import pytest

from conftest import D0, D1, D2, at, stock
from portfolio_rebalancer import (
    AbsoluteBand,
    AllOf,
    Allocation,
    AlwaysTrigger,
    CalendarTrigger,
    DriftTrigger,
    FiveTwentyFive,
    FixedPlusPercentCost,
    MarketContext,
    Portfolio,
    PriceHistory,
    PricePoint,
    RatePoint,
    RebalancePolicy,
    RelativeBand,
    RiskFreeRate,
    Sector,
    Side,
    StalePlanError,
    TargetMode,
    calendar_policy,
    default_policy,
)

TO_TARGET = RebalancePolicy(trigger=AlwaysTrigger(), target_mode=TargetMode.TO_TARGET, use_cash_flows_first=False)


def orders_by_symbol(plan):
    return {o.symbol: (o.side, o.quantity) for o in plan.orders}


def invested(portfolio):
    """Buy the initial 40/60 with the 10,000 deposit, at D1 prices."""
    portfolio.apply(portfolio.rebalance(TO_TARGET, as_of=D1))
    return portfolio


class TestBands:
    def test_absolute_and_relative(self):
        assert AbsoluteBand(Decimal("0.05")).bounds(Decimal("0.4")) == (Decimal("0.35"), Decimal("0.45"))
        assert RelativeBand(Decimal("0.25")).bounds(Decimal("0.4")) == (Decimal("0.3"), Decimal("0.5"))

    def test_five_twenty_five_uses_tighter_band(self):
        band = FiveTwentyFive()
        assert band.bounds(Decimal("0.4")) == (Decimal("0.35"), Decimal("0.45"))  # 5pp < 10pp
        assert band.bounds(Decimal("0.08")) == (Decimal("0.06"), Decimal("0.10"))  # 2pp < 5pp

    def test_zero_target_has_zero_band(self):
        assert FiveTwentyFive().bounds(Decimal("0")) == (0, 0)


class TestRequirementExample:
    """The task's example: a 40% META / 60% AAPL target."""

    def test_initial_investment_buys_target(self, funded):
        plan = funded.rebalance(TO_TARGET, as_of=D1)
        assert orders_by_symbol(plan) == {"META": (Side.BUY, 40), "AAPL": (Side.BUY, 30)}
        funded.apply(plan)
        assert funded.cash() == 0
        assert funded.last_rebalanced_at == D1

    def test_drift_sells_winner_and_buys_laggard(self, funded):
        invested(funded)
        # META +50%: 6000 META / 6000 AAPL = 50/50 vs 40/60 target
        plan = funded.rebalance(TO_TARGET, as_of=D2)
        assert orders_by_symbol(plan) == {"META": (Side.SELL, 8), "AAPL": (Side.BUY, 6)}
        assert plan.orders[0].side is Side.SELL
        assert plan.weights_after == {"META": Decimal("0.4"), "AAPL": Decimal("0.6")}

    def test_within_band_does_nothing(self, funded, meta):
        invested(funded)
        meta.update_price(105, D2 + timedelta(days=1))  # 41% META, inside 35..45
        plan = funded.rebalance(as_of=D2 + timedelta(days=1))
        assert not plan.triggered and plan.is_empty
        assert "within tolerance" in plan.reasons[-1]


class TestDefaultPolicy:
    def test_trades_to_band_edge_not_target(self, funded):
        invested(funded)
        plan = funded.rebalance(default_policy(), as_of=D2)
        assert plan.triggered
        # META 50% -> upper edge 45% (sell 4 x 150 = 600), AAPL gets the proceeds
        assert orders_by_symbol(plan) == {"META": (Side.SELL, 4), "AAPL": (Side.BUY, 3)}
        assert plan.weights_after["META"] == Decimal("0.45")

    def test_waits_for_the_check_interval(self, funded, meta):
        invested(funded)
        month_later = D1 + timedelta(days=30)
        meta.update_price(150, month_later)
        assert not funded.rebalance(default_policy(), as_of=month_later).triggered
        assert funded.rebalance(default_policy(check_every=timedelta(days=30)), as_of=month_later).triggered

    def test_regime_change_is_checked_every_day(self, funded):
        rates = RiskFreeRate("T-Bill", "3M", [RatePoint(D0, "0.02"), RatePoint(D2, "0.04")])
        market = MarketContext(rates, PriceHistory([PricePoint(D0, 100)]))
        funded.set_allocation(Allocation.from_percentages({"META": 40, "AAPL": 60}).with_context(market.as_of(D0)))
        invested(funded)
        years = timedelta(days=3650)
        plan = funded.rebalance(default_policy(check_every=years), as_of=D2, market=market)
        assert plan.requires_reoptimization and "+200bps" in plan.reasons[0]
        without = funded.rebalance(default_policy(check_every=years, detect_regime_change=False), as_of=D2, market=market)
        assert not without.triggered

    def test_whole_shares_land_inside_band(self):
        # Coarse shares (one META share ~1.4% of the portfolio): exact band edges are unreachable.
        meta = stock("META", {D1: 970, D2: 1300}, Sector.COMMUNICATION_SERVICES)
        aapl = stock("AAPL", {D1: 1000, D2: 1000})
        portfolio = Portfolio("x", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl])
        portfolio.deposit(70_000, at(D0))
        portfolio.apply(portfolio.rebalance(TO_TARGET, as_of=D1))
        plan = portfolio.rebalance(default_policy(), as_of=D2)
        assert plan.triggered and plan.orders
        for symbol, weight in plan.weights_after.items():
            low, high = FiveTwentyFive().bounds(plan.target_weights[symbol])
            assert low <= weight <= high, symbol

    def test_uses_cash_flows_before_selling(self, funded):
        invested(funded)
        funded.deposit(3_000, at(D2))
        plan = funded.rebalance(default_policy(), as_of=D2)
        assert all(o.side is Side.BUY for o in plan.orders)
        assert "no sales needed" in plan.reasons[-1]
        low, high = FiveTwentyFive().bounds(Decimal("0.4"))
        assert low <= plan.weights_after["META"] <= high

    def test_sector_cap_forces_sector_back_to_target(self, funded):
        invested(funded)
        caps = {Sector.COMMUNICATION_SERVICES: Decimal("0.42")}
        policy = default_policy(band=AbsoluteBand(Decimal("0.2")), sector_limits=caps)  # wide band: only the cap fires
        plan = funded.rebalance(policy, as_of=D2)
        assert any("exceeds" in r for r in plan.reasons)
        assert plan.sector_weights_after[Sector.COMMUNICATION_SERVICES] == Decimal("0.4")


class TestTargetChanges:
    def test_new_instrument_is_bought_and_removed_one_liquidated(self, funded):
        invested(funded)
        msft = stock("MSFT", {D0: 400, D2: 400})
        funded.set_allocation(Allocation.from_percentages({"AAPL": 60, "MSFT": 40}), new_stocks=[msft])
        plan = funded.rebalance(as_of=D2)
        orders = orders_by_symbol(plan)
        assert orders["META"] == (Side.SELL, 40)
        assert next(o for o in plan.orders if o.symbol == "META").liquidation
        assert orders["MSFT"][0] is Side.BUY

    def test_new_target_is_executed_fully_even_with_band_edge_policy(self, funded):
        invested(funded)
        funded.set_allocation(Allocation.from_percentages({"META": 50, "AAPL": 50}))
        assert funded.allocation_pending
        plan = funded.rebalance(default_policy(), as_of=D1)
        assert plan.triggered and "not executed yet" in plan.reasons[-1]
        assert plan.weights_after == {"META": Decimal("0.5"), "AAPL": Decimal("0.5")}
        funded.apply(plan)
        assert not funded.allocation_pending

    def test_plan_for_previous_target_is_stale(self, funded):
        plan = funded.rebalance(TO_TARGET, as_of=D1)
        funded.set_allocation(Allocation.from_percentages({"META": 50, "AAPL": 50}))
        with pytest.raises(StalePlanError, match="target allocation changed"):
            funded.apply(plan)

    def test_liquidation_ignores_minimum_trade_value(self, funded):
        invested(funded)
        funded.set_allocation(Allocation.from_percentages({"AAPL": 100}))
        policy = default_policy(min_trade_value=Decimal("1000000"))
        assert orders_by_symbol(funded.rebalance(policy, as_of=D2))["META"] == (Side.SELL, 40)


class TestExecutionConstraints:
    def test_whole_shares_leave_residual_cash(self, meta, aapl):
        portfolio = Portfolio("x", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl])
        portfolio.deposit(1_050, at(D0))
        plan = portfolio.rebalance(TO_TARGET, as_of=D1)
        assert orders_by_symbol(plan) == {"META": (Side.BUY, 4), "AAPL": (Side.BUY, 3)}
        assert plan.cash_after == Decimal("50")

    def test_fractional_shares(self, meta, aapl):
        portfolio = Portfolio("x", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl])
        portfolio.deposit(1_050, at(D0))
        policy = RebalancePolicy(AlwaysTrigger(), target_mode=TargetMode.TO_TARGET, fractional_shares=True)
        plan = portfolio.rebalance(policy, as_of=D1)
        assert orders_by_symbol(plan)["META"] == (Side.BUY, Decimal("4.2"))
        assert plan.cash_after == 0

    def test_costs_are_reserved_and_reported(self, meta, aapl):
        portfolio = Portfolio(
            "x", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl],
            cost_model=FixedPlusPercentCost(Decimal("1"), Decimal("0.001")),
        )
        portfolio.deposit(10_000, at(D0))
        plan = portfolio.rebalance(TO_TARGET, as_of=D1)
        assert plan.cash_after >= 0
        assert plan.estimated_costs > 0
        portfolio.apply(plan)
        assert sum(t.fee for t in portfolio.transactions) == plan.estimated_costs

    def test_tiny_trades_are_skipped_with_reason(self, funded):
        invested(funded)
        plan = funded.rebalance(default_policy(min_trade_value=Decimal("5000")), as_of=D2)
        assert plan.is_empty
        assert {s.reason for s in plan.skipped} == {"below minimum trade value"}

    def test_expensive_trades_are_skipped(self, meta, aapl):
        portfolio = Portfolio(
            "x", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl],
            cost_model=FixedPlusPercentCost(Decimal("50")),
        )
        portfolio.deposit(1_000, at(D0))
        plan = portfolio.rebalance(TO_TARGET, as_of=D1)
        assert plan.is_empty and len(plan.skipped) == 2

    def test_cash_buffer_is_kept(self, funded):
        policy = RebalancePolicy(AlwaysTrigger(), target_mode=TargetMode.TO_TARGET, cash_buffer=Decimal("0.05"))
        plan = funded.rebalance(policy, as_of=D1)
        assert plan.cash_after >= Decimal("500")


class TestTriggers:
    def test_calendar_policy(self, funded):
        invested(funded)
        policy = calendar_policy(every=timedelta(days=365))
        assert not funded.rebalance(policy, as_of=D2).triggered
        assert funded.rebalance(policy, as_of=D1 + timedelta(days=365)).triggered

    def test_hybrid_needs_both_time_and_drift(self, funded):
        invested(funded)
        hybrid = RebalancePolicy(AllOf(CalendarTrigger(timedelta(days=90)), DriftTrigger()))
        assert funded.rebalance(hybrid, as_of=D2).triggered  # 150 days and META out of band
        early = RebalancePolicy(AllOf(CalendarTrigger(timedelta(days=365)), DriftTrigger()))
        assert not funded.rebalance(early, as_of=D2).triggered


class TestApply:
    def test_stale_plan_is_rejected(self, funded):
        plan = funded.rebalance(TO_TARGET, as_of=D1)
        funded.deposit(100, at(D1))
        with pytest.raises(StalePlanError):
            funded.apply(plan)

    def test_rebalance_does_not_mutate(self, funded):
        before = funded.transactions
        funded.rebalance(TO_TARGET, as_of=D1)
        assert funded.transactions == before

    def test_summary_is_readable(self, funded):
        text = funded.rebalance(TO_TARGET, as_of=D1).summary()
        assert "BUY  META" in text and "Target" in text

    def test_empty_portfolio(self, meta, aapl):
        portfolio = Portfolio("x", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl])
        plan = portfolio.rebalance(as_of=D1)
        assert not plan.triggered and plan.is_empty
