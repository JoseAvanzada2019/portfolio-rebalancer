"""Backtest of rebalancing policies on real data. Writes results.md and docs/backtest_*.png.

    .venv/bin/pip install -e ".[backtest]"
    .venv/bin/python lib/examples/demo.py

Prices and rates are read from the data/ cache; anything missing is downloaded first.
"""

from __future__ import annotations

import math
import re
import sys
import warnings
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio_rebalancer import (  # noqa: E402
    AbsoluteBand,
    AllOf,
    Allocation,
    CalendarTrigger,
    DriftTrigger,
    FixedPlusPercentCost,
    HistoricalRiskModel,
    InverseVolatility,
    MarketContext,
    NeverTrigger,
    Portfolio,
    RebalancePolicy,
    Sector,
    Stock,
    TargetMode,
    calendar_policy,
    default_policy,
)
from portfolio_rebalancer.stats import TRADING_DAYS_PER_YEAR, mean, stdev  # noqa: E402

import real_data  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
START, END = date(2015, 1, 2), date(2025, 12, 31)
DOWNLOAD_FROM = date(2014, 1, 1)
INITIAL_CASH = Decimal("100000")
COSTS = FixedPlusPercentCost(Decimal("1"), Decimal("0.0005"))
RISK_TARGET = Decimal("0.20")
ROLLING_WINDOW = 63
STABLE_BAND = 0.02

UNIVERSES: dict[str, dict[str, tuple[str, Sector, Decimal]]] = {
    "META/AAPL 40/60": {
        "META": ("Meta Platforms, Inc.", Sector.COMMUNICATION_SERVICES, Decimal("0.4")),
        "AAPL": ("Apple Inc.", Sector.INFORMATION_TECHNOLOGY, Decimal("0.6")),
    },
    "Diversified, 7 stocks equal weight": {
        symbol: (name, sector, Decimal(1) / 7)
        for symbol, name, sector in [
            ("AAPL", "Apple Inc.", Sector.INFORMATION_TECHNOLOGY),
            ("MSFT", "Microsoft Corporation", Sector.INFORMATION_TECHNOLOGY),
            ("JPM", "JPMorgan Chase & Co.", Sector.FINANCIALS),
            ("XOM", "Exxon Mobil Corporation", Sector.ENERGY),
            ("JNJ", "Johnson & Johnson", Sector.HEALTH_CARE),
            ("NEE", "NextEra Energy, Inc.", Sector.UTILITIES),
            ("PG", "Procter & Gamble Company", Sector.CONSUMER_STAPLES),
        ]
    },
}

FIVE_PP = AbsoluteBand(Decimal("0.05"))
POLICIES: list[tuple[str, RebalancePolicy, bool]] = [
    ("Buy and hold", RebalancePolicy(NeverTrigger()), False),
    ("Calendar monthly", calendar_policy(timedelta(days=30)), False),
    ("Calendar quarterly", calendar_policy(timedelta(days=91)), False),
    ("Calendar yearly", calendar_policy(timedelta(days=365)), False),
    ("Threshold ±5pp, to target", RebalancePolicy(DriftTrigger(FIVE_PP), band=FIVE_PP, target_mode=TargetMode.TO_TARGET), False),
    ("Hybrid quarterly + 5/25", RebalancePolicy(AllOf(CalendarTrigger(timedelta(days=91)), DriftTrigger())), False),
    ("Default: 5/25, band edge", replace(default_policy(), trigger=DriftTrigger()), False),
    ("Default + regime re-optimization", default_policy(), True),
]


@dataclass
class Run:
    policy: str
    days: list[date]
    values: list[float] = field(default_factory=list)
    distances: list[float] = field(default_factory=list)
    rebalances: int = 0
    trades: int = 0
    traded_value: float = 0.0
    costs: float = 0.0
    reoptimizations: int = 0


def simulate(name: str, policy: RebalancePolicy, regime_aware: bool, stocks: list[Stock],
             weights: dict[str, Decimal], market: MarketContext, days: list[date]) -> Run:
    allocation = Allocation(weights, label="initial").with_context(market.as_of(days[0]))
    portfolio = Portfolio(name, allocation, stocks, cost_model=COSTS, risk_target=RISK_TARGET)
    portfolio.deposit(INITIAL_CASH, datetime.combine(days[0], time.min, tzinfo=timezone.utc))
    run = Run(name, days)
    for day in days:
        plan = portfolio.rebalance(policy, day, market)
        if plan.requires_reoptimization and regime_aware:
            strategy = InverseVolatility(HistoricalRiskModel(stocks), RISK_TARGET, label=f"inverse-volatility {day}")
            portfolio.set_allocation(strategy.build(stocks, market.as_of(day), day))
            run.reoptimizations += 1
            plan = portfolio.rebalance(policy, day, market)
        if plan.orders:
            portfolio.apply(plan)
            run.rebalances += 1
            run.trades += len(plan.orders)
            run.traded_value += float(sum(o.value for o in plan.orders))
            run.costs += float(plan.estimated_costs)
        total = plan.total_value_before - plan.estimated_costs
        cash_gap = abs(plan.cash_after / total - portfolio.allocation.cash_weight)
        symbol_gap = sum(abs(w - plan.target_weights.get(s, Decimal(0))) for s, w in plan.weights_after.items())
        run.values.append(float(total))
        run.distances.append(float(symbol_gap + cash_gap) / 2)
    return run


def reference_run(symbol: str, stocks_market: MarketContext, days: list[date]) -> Run:
    run = Run(f"{symbol} (reference, no costs)", days)
    start = stocks_market.benchmark.at(days[0]).close
    run.values = [float(stocks_market.benchmark.at(d).close / start) * float(INITIAL_CASH) for d in days]
    return run


def rolling_volatility(returns: list[float]) -> list[float]:
    scale = math.sqrt(TRADING_DAYS_PER_YEAR)
    return [stdev(returns[i - ROLLING_WINDOW:i]) * scale for i in range(ROLLING_WINDOW, len(returns) + 1)]


def metrics(run: Run, risk_free_daily: list[float]) -> dict[str, str]:
    values = run.values
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    excess = [r - rf for r, rf in zip(returns, risk_free_daily[1:])]
    years = (run.days[-1] - run.days[0]).days / 365.25
    volatility = stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
    peak, max_drawdown = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        max_drawdown = min(max_drawdown, v / peak - 1)
    rolling = rolling_volatility(returns)
    within = sum(abs(v - volatility) <= STABLE_BAND for v in rolling) / len(rolling)
    reference = not run.distances
    return {
        "Policy": run.policy,
        "CAGR": f"{(values[-1] / values[0]) ** (1 / years) - 1:.2%}",
        "Volatility": f"{volatility:.2%}",
        "Sharpe": f"{mean(excess) / stdev(excess) * math.sqrt(TRADING_DAYS_PER_YEAR):.2f}",
        "Max drawdown": f"{max_drawdown:.2%}",
        "Vol of 63d vol": f"{stdev(rolling):.2%}",
        "Days within ±2pp of vol": f"{within:.0%}",
        "Avg distance to target": "-" if reference else f"{mean(run.distances):.2%}",
        "Rebalances": "-" if reference else str(run.rebalances),
        "Trades": "-" if reference else str(run.trades),
        "Turnover / yr": "-" if reference else f"{run.traded_value / mean(values) / years:.0%}",
        "Costs": "-" if reference else f"{run.costs:,.0f}",
        "Re-optimizations": "-" if reference else str(run.reoptimizations),
    }


def markdown_table(rows: list[dict[str, str]]) -> str:
    headers = list(rows[0])
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" if h == "Policy" else "--:" for h in headers) + "|"]
    lines += ["| " + " | ".join(row[h] for h in headers) + " |" for row in rows]
    return "\n".join(lines)


def plot(universe: str, runs: list[Run], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for run in runs:
        style = "--" if not run.distances else "-"
        top.plot(run.days, run.values, style, label=run.policy, linewidth=1)
        returns = [b / a - 1 for a, b in zip(run.values, run.values[1:])]
        bottom.plot(run.days[ROLLING_WINDOW:], rolling_volatility(returns), style, linewidth=0.8)
    top.set_yscale("log")
    top.set_ylabel("Portfolio value (log)")
    top.legend(fontsize=7, ncol=2)
    top.set_title(universe)
    bottom.set_ylabel("63-day rolling volatility")
    bottom.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    fig.tight_layout()
    path.parent.mkdir(exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def existing_findings(path: Path) -> str | None:
    if not path.exists():
        return None
    match = re.search(r"^## Findings\n(.*?)(?=^## |\Z)", path.read_text(), flags=re.S | re.M)
    return match.group(1).strip() if match else None


def main() -> None:
    symbols = sorted({s for universe in UNIVERSES.values() for s in universe} | {"SPY"})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        real_data.download(symbols, DOWNLOAD_FROM, END + timedelta(days=1))
    provider = real_data.CachedMarketDataProvider()
    days = [d for d in provider.benchmark().dates() if START <= d <= END]
    month_starts = [d for prev, d in zip([None] + days, days) if prev is None or prev.month != d.month]

    sections = []
    for universe, spec in UNIVERSES.items():
        stocks = [provider.stock(symbol, name, sector) for symbol, (name, sector, _) in spec.items()]
        weights = {symbol: weight for symbol, (_, _, weight) in spec.items()}
        market = provider.market_context()
        for day in month_starts:
            market.record_sector_snapshots(stocks, day)
        risk_free_daily = [float(market.risk_free.periodic(d)) for d in days]

        runs = []
        for name, policy, regime_aware in POLICIES:
            print(f"{universe}: {name}", flush=True)
            runs.append(simulate(name, policy, regime_aware, stocks, weights, market, days))
        reference = reference_run("SPY", market, days)
        slug = re.sub(r"[^a-z0-9]+", "_", universe.lower()).strip("_")
        plot(universe, runs + [reference], ROOT / "docs" / f"backtest_{slug}.png")
        table = markdown_table([metrics(run, risk_free_daily) for run in runs + [reference]])
        sections.append(f"### {universe}\n\n{table}\n\n![{universe}](docs/backtest_{slug}.png)")

    results = ROOT / "results.md"
    findings = existing_findings(results) or "_To be written after reviewing the tables._"
    results.write_text(RESULTS_TEMPLATE.format(
        start=days[0], end=days[-1], sections="\n\n".join(sections), findings=findings,
    ))
    print(f"wrote {results}")


RESULTS_TEMPLATE = """# Backtest results

Rebalancing policies compared on real data, {start} to {end}.
Reproduce with `.venv/bin/python lib/examples/demo.py` (see the README).

## Setup

* **Prices**: Yahoo Finance daily closes adjusted for splits and dividends
  (total return), cached under `data/`. **Risk-free rate**: FRED `DTB3`
  (3-month T-bill). **Benchmark**: SPY.
* 100,000 initial cash, invested on the first day. Every trading day each
  policy is asked for a plan, and the plan is filled at that day's close.
* Costs: 1 USD per order + 5bps of traded value. Whole shares.
* Sector risk snapshots (252-day window) are recorded on the first trading
  day of each month; every lookup is point-in-time.
* "Default + regime re-optimization": when the regime trigger fires (risk-free
  rate moved 100bps or more, or a sector's beta, volatility or rate
  sensitivity changed beyond its threshold since the target was built), the
  target is rebuilt as inverse-volatility under a 20% volatility target and
  traded to. Its target therefore differs from the others' after the first
  re-optimization.

## Metrics

* **Vol of 63d vol**: standard deviation of the 63-day rolling annualized
  volatility. Lower means more stable risk, which is the objective.
* **Days within ±2pp of vol**: share of days whose rolling volatility is
  within 2 percentage points of the full-period volatility.
* **Avg distance to target**: half the sum of absolute differences between
  current and target weights (cash included), i.e. the fraction of the
  portfolio that sits in the wrong place, averaged over all days.
* **Turnover / yr**: traded value divided by the average portfolio value, per year.
* Sharpe uses daily excess returns over `DTB3`.

## Results

{sections}

## Findings

{findings}

## Caveats

* Survivorship bias: both universes are made of companies that are large
  today; their past returns are not a random draw.
* No taxes. Selling winners would realize capital gains, which penalizes the
  policies that trade more.
* Fills at the close, with no market impact. Order sizes here are small, but
  see "Next steps" in the README.
* Yahoo's adjusted prices are reconstructed after the fact and can contain errors.
"""


if __name__ == "__main__":
    main()
