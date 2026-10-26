from datetime import date
from decimal import Decimal

import pytest

from conftest import at
from portfolio_rebalancer import (
    Allocation,
    HistoricalRiskModel,
    InMemoryMarketDataProvider,
    InverseVolatility,
    ManualAllocation,
    MarketSnapshot,
    Portfolio,
    RatePoint,
    RegimeChangeTrigger,
    RiskFreeRate,
    Sector,
    rank_by_sharpe,
)
from portfolio_rebalancer.market.synthetic import business_days, synthetic_prices
from portfolio_rebalancer.market.sector_risk import SectorRiskSnapshot
from portfolio_rebalancer.rebalancing.triggers import TriggerContext

START = date(2024, 1, 1)
DAYS = 300
DATES = business_days(START, DAYS)
END = DATES[-1]


@pytest.fixture
def provider():
    benchmark = synthetic_prices(START, DAYS, 100, 0.08, 0.15, seed=1)
    prices = {
        "CALM": synthetic_prices(START, DAYS, 50, 0.06, 0.10, seed=2),
        "WILD": synthetic_prices(START, DAYS, 50, 0.12, 0.45, seed=3),
        "MKT": benchmark,  # a stock identical to the benchmark
    }
    rates = [RatePoint(START, "0.02"), RatePoint(DATES[150], "0.045")]
    return InMemoryMarketDataProvider(prices, rates, benchmark, "US 3M T-Bill")


class TestRiskFreeRate:
    def test_point_in_time(self):
        rf = RiskFreeRate("US 3M T-Bill", "3M", [RatePoint(date(2024, 1, 1), "0.05"), RatePoint(date(2024, 6, 1), "0.04")])
        assert rf.rate_at(date(2024, 5, 31)) == Decimal("0.05")
        assert rf.rate_at(date(2024, 7, 1)) == Decimal("0.04")
        assert rf.change_bps(date(2024, 1, 1), date(2024, 7, 1)) == Decimal("-100")
        with pytest.raises(LookupError):
            rf.rate_at(date(2023, 12, 31))

    def test_periodic_rate_compounds_back_to_annual(self):
        rf = RiskFreeRate("x", "3M", [RatePoint(date(2024, 1, 1), "0.05")])
        daily = rf.periodic(date(2024, 1, 1), 252)
        assert (1 + float(daily)) ** 252 - 1 == pytest.approx(0.05, rel=1e-6)


class TestSectorRisk:
    def test_snapshot_of_benchmark_has_beta_one(self, provider):
        market = provider.market_context()
        mkt = provider.stock("MKT", "Market", Sector.FINANCIALS)
        (snapshot,) = market.record_sector_snapshots([mkt], END, window=200)
        assert snapshot.beta_to_market == pytest.approx(1.0)
        assert snapshot.risk_free_at_snapshot == Decimal("0.045")
        assert snapshot.window == 200

    def test_history_is_append_only_and_point_in_time(self, provider):
        market = provider.market_context()
        calm = provider.stock("CALM", "Calm Co", Sector.UTILITIES)
        market.record_sector_snapshots([calm], DATES[200], window=100)
        market.record_sector_snapshots([calm], END, window=100)
        with pytest.raises(ValueError, match="append-only"):
            market.record_sector_snapshots([calm], DATES[250], window=100)
        assert market.as_of(DATES[220]).sector_risk[Sector.UTILITIES].date == DATES[200]
        assert len(market.sector_risk.series(Sector.UTILITIES, "rate_sensitivity")) == 2

    def test_snapshot_feeds_market_snapshot(self, provider):
        market = provider.market_context()
        market.record_sector_snapshots([provider.stock("CALM", "Calm", Sector.UTILITIES)], END, window=100)
        snapshot = market.as_of(END)
        assert snapshot.risk_free_rate == Decimal("0.045")
        assert Sector.UTILITIES in snapshot.sector_risk


def _sector_snapshot(on, beta=1.0, vol=0.2, sens=0.0):
    return SectorRiskSnapshot(on, Sector.INFORMATION_TECHNOLOGY, beta, vol, 0.05, 0.25, sens, Decimal("0.04"), 252)


class TestRegimeChange:
    def _ctx(self, allocation, market):
        return TriggerContext(date(2025, 1, 1), {}, allocation, {}, None, market)

    def test_risk_free_move_requires_reoptimization(self):
        allocation = Allocation.from_percentages({"META": 100}).with_context(MarketSnapshot(date(2024, 1, 1), Decimal("0.03")))
        result = RegimeChangeTrigger().evaluate(self._ctx(allocation, MarketSnapshot(date(2025, 1, 1), Decimal("0.045"))))
        assert result.triggered and result.requires_reoptimization
        assert "+150bps" in result.reasons[0]

    def test_sector_rate_sensitivity_change(self):
        then = MarketSnapshot(date(2024, 1, 1), Decimal("0.04"), {Sector.INFORMATION_TECHNOLOGY: _sector_snapshot(date(2024, 1, 1))})
        now = MarketSnapshot(date(2025, 1, 1), Decimal("0.04"), {Sector.INFORMATION_TECHNOLOGY: _sector_snapshot(date(2025, 1, 1), sens=-0.05)})
        allocation = Allocation.from_percentages({"AAPL": 100}).with_context(then)
        result = RegimeChangeTrigger().evaluate(self._ctx(allocation, now))
        assert result.requires_reoptimization
        assert "rate sensitivity" in result.reasons[0]

    def test_small_moves_are_ignored(self):
        allocation = Allocation.from_percentages({"META": 100}).with_context(MarketSnapshot(date(2024, 1, 1), Decimal("0.040")))
        result = RegimeChangeTrigger().evaluate(self._ctx(allocation, MarketSnapshot(date(2025, 1, 1), Decimal("0.045"))))
        assert not result.triggered

    def test_portfolio_does_not_trade_to_stale_target(self, provider):
        calm = provider.stock("CALM", "Calm", Sector.UTILITIES)
        built_with = provider.market_context().as_of(DATES[10])  # rf 2%
        portfolio = Portfolio("x", Allocation.from_percentages({"CALM": 100}).with_context(built_with), [calm])
        portfolio.deposit(1_000, at(DATES[0]))
        plan = portfolio.rebalance(as_of=END, market=provider.market_context())  # rf now 4.5%
        assert plan.requires_reoptimization and plan.is_empty
        assert plan.risk_free_rate == Decimal("0.045")


class TestStrategies:
    def test_inverse_volatility_favors_calm_stock(self, provider):
        stocks = [provider.stock("CALM", "Calm", Sector.UTILITIES), provider.stock("WILD", "Wild", Sector.ENERGY)]
        allocation = InverseVolatility(HistoricalRiskModel(stocks)).build(stocks, None, END)
        assert allocation.weight("CALM") > allocation.weight("WILD")
        assert sum(allocation.weights.values()) == 1

    def test_risk_target_adds_cash_and_hits_target(self, provider):
        stocks = [provider.stock("WILD", "Wild", Sector.ENERGY)]
        model = HistoricalRiskModel(stocks)
        allocation = InverseVolatility(model, risk_target=Decimal("0.15")).build(stocks, None, END)
        assert allocation.cash_weight > 0
        assert model.portfolio_volatility(allocation.weights, END) == pytest.approx(0.15, abs=1e-4)

    def test_allocation_records_market_context(self, provider):
        snapshot = provider.market_context().as_of(END)
        allocation = ManualAllocation({"CALM": 1}).build([], snapshot)
        assert allocation.risk_free_used == Decimal("0.045")

    def test_rank_by_sharpe_uses_risk_free_rate(self, provider):
        stocks = [provider.stock(s, s, Sector.ENERGY) for s in ("CALM", "WILD", "MKT")]
        ranking = rank_by_sharpe(stocks, Decimal("0.045"), END)
        sharpes = [sharpe for _, sharpe in ranking]
        assert sharpes == sorted(sharpes, reverse=True)
        low_rf = dict(rank_by_sharpe(stocks, Decimal("0"), END))
        assert all(low_rf[s] > sharpe for s, sharpe in ranking)  # higher rf, lower Sharpe

    def test_portfolio_volatility_diversifies(self, provider):
        stocks = [provider.stock("CALM", "Calm", Sector.UTILITIES), provider.stock("WILD", "Wild", Sector.ENERGY)]
        model = HistoricalRiskModel(stocks)
        mixed = model.portfolio_volatility({"CALM": Decimal("0.5"), "WILD": Decimal("0.5")}, END)
        assert mixed < 0.5 * model.volatility("CALM", END) + 0.5 * model.volatility("WILD", END)
