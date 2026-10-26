"""Feature walkthrough on synthetic data: python lib/examples/walkthrough.py

1. Fund a 40/60 META/AAPL portfolio and invest it.
2. Prices drift: the default policy trims to the band edge.
3. The risk-free rate jumps: the plan refuses to trade to a stale target.
4. Re-optimize with a new instrument (MSFT) vetted by Sharpe ratio, under a volatility target.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio_rebalancer import (  # noqa: E402
    Allocation,
    AlwaysTrigger,
    FixedPlusPercentCost,
    HistoricalRiskModel,
    InMemoryMarketDataProvider,
    InverseVolatility,
    Portfolio,
    RatePoint,
    RebalancePolicy,
    Sector,
    TargetMode,
    default_policy,
    rank_by_sharpe,
)
from portfolio_rebalancer.market.synthetic import business_days, synthetic_prices  # noqa: E402

START, DAYS = datetime(2024, 1, 1).date(), 520
DATES = business_days(START, DAYS)
INVEST_DAY, DRIFT_DAY, END = DATES[260], DATES[400], DATES[-1]


def header(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


provider = InMemoryMarketDataProvider(
    prices={
        "META": synthetic_prices(START, DAYS, 350, 0.30, 0.35, seed=4),
        "AAPL": synthetic_prices(START, DAYS, 185, 0.05, 0.25, seed=105),
        "MSFT": synthetic_prices(START, DAYS, 370, 0.25, 0.18, seed=205),
    },
    risk_free=[RatePoint(START, "0.020"), RatePoint(DATES[450], "0.045")],
    benchmark=synthetic_prices(START, DAYS, 4700, 0.10, 0.16, seed=10),
    risk_free_source="US 3M T-Bill",
)
meta = provider.stock("META", "Meta Platforms, Inc.", Sector.COMMUNICATION_SERVICES)
aapl = provider.stock("AAPL", "Apple Inc.", Sector.INFORMATION_TECHNOLOGY)
msft = provider.stock("MSFT", "Microsoft Corporation", Sector.INFORMATION_TECHNOLOGY)
market = provider.market_context()
market.record_sector_snapshots([meta, aapl], INVEST_DAY)

header("1. Invest 100,000 as 40% META / 60% AAPL")
allocation = Allocation.from_percentages({"META": 40, "AAPL": 60}, label="initial").with_context(market.as_of(INVEST_DAY))
portfolio = Portfolio(
    "demo", allocation, [meta, aapl],
    cost_model=FixedPlusPercentCost(Decimal("1.00"), Decimal("0.0005")),
    risk_target=Decimal("0.20"),
)
portfolio.deposit(100_000, datetime.combine(INVEST_DAY, datetime.min.time(), tzinfo=timezone.utc))
initial = portfolio.rebalance(RebalancePolicy(AlwaysTrigger(), target_mode=TargetMode.TO_TARGET), INVEST_DAY, market)
print(initial.summary())
portfolio.apply(initial)

header(f"2. {DRIFT_DAY}: prices drifted, default policy (quarterly check, 5/25 bands, to band edge)")
drift_plan = portfolio.rebalance(default_policy(), DRIFT_DAY, market)
print(drift_plan.summary())
portfolio.apply(drift_plan)

header(f"3. {END}: risk-free rate jumped from 2.0% to 4.5%")
market.record_sector_snapshots([meta, aapl], END)
stale = portfolio.rebalance(default_policy(), END, market)
print(stale.summary())

header("4. Re-optimize: vet MSFT by Sharpe, inverse-volatility under a 20% volatility target")
snapshot = market.as_of(END)
for symbol, sharpe in rank_by_sharpe([meta, aapl, msft], snapshot.risk_free_rate, END):
    print(f"  Sharpe {symbol}: {sharpe:5.2f}")
universe = [meta, msft]  # AAPL has the worst risk-adjusted return: replaced by MSFT
strategy = InverseVolatility(HistoricalRiskModel(universe), risk_target=portfolio.risk_target, label="inv-vol 2025")
new_allocation = strategy.build(universe, snapshot, END)
print("  new target:", {s: f"{w:.2%}" for s, w in new_allocation.weights.items()}, f"cash {new_allocation.cash_weight:.2%}")
ex_ante = HistoricalRiskModel(universe).portfolio_volatility(new_allocation.weights, END)
print(f"  ex-ante volatility {ex_ante:.2%} vs target {portfolio.risk_target:.0%}")
portfolio.set_allocation(new_allocation, new_stocks=[msft])
reoptimized = portfolio.rebalance(default_policy(), END, market)
print(reoptimized.summary())
portfolio.apply(reoptimized)

header("Ledger")
for tx in portfolio.transactions:
    detail = f"{tx.quantity} {tx.symbol} @ {tx.price}" if tx.symbol else f"{tx.amount}"
    print(f"  {tx.timestamp:%Y-%m-%d} {tx.type.value:<10} {detail}  fee {tx.fee}")
print(f"\nAllocations used: {[a.label for a in portfolio.allocation_history]}")
