# Portfolio Rebalancer

A small, dependency-free Python library for the portfolio-management module of
a personal investments app. A `Portfolio` holds `Stock`s and a target
`Allocation` (e.g. 40% META / 60% AAPL); `portfolio.rebalance()` says which
stocks to sell and which to buy to get back to that allocation, and explains why.

```python
from portfolio_rebalancer import Allocation, Portfolio, default_policy

portfolio = Portfolio("main", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl])
portfolio.deposit(10_000)

plan = portfolio.rebalance()      # pure: proposes orders, changes nothing
print(plan.summary())             # orders, reasons, weights before/after, costs
portfolio.apply(plan)             # records the trades in the ledger
```

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev,backtest]"
.venv/bin/python -m pytest                    # 60 tests
.venv/bin/python lib/examples/walkthrough.py  # feature tour on synthetic data
.venv/bin/python lib/examples/demo.py         # backtest on real data -> results.md
```

Requires Python 3.10+. The library has no runtime dependencies; `yfinance`
and `matplotlib` are only used by the backtest.

**Backtest results (2015–2025, real data):** [results.md](results.md).
**Conversation with the LLM used to build this:** [docs/llm-conversation.md](docs/llm-conversation.md).

## Repository structure

```
.
├── README.md                      this file: thinking process, design, tests, next steps
├── results.md                     backtest results on real data (generated + findings)
├── pyproject.toml                 package metadata; extras: dev (pytest), backtest (yfinance, matplotlib)
├── docs/
│   ├── llm-conversation.md        the full design conversation with Claude
│   └── backtest_*.png             value and rolling-volatility charts per universe
├── data/                          downloaded price/rate cache (git-ignored, created by the backtest)
└── lib/
    ├── portfolio_rebalancer/      the library
    │   ├── money.py               Decimal constants and conversion
    │   ├── stats.py               float statistics (volatility, covariance, beta)
    │   ├── domain/                what the user owns
    │   │   ├── enums.py           Sector (GICS), TransactionType
    │   │   ├── exceptions.py      LedgerError, StalePlanError
    │   │   ├── stock.py           Stock: symbol, name, sector, price history, current_price()
    │   │   ├── transaction.py     immutable ledger entries
    │   │   ├── ledger.py          time-ordered movements; positions and cash replayed from them
    │   │   ├── position.py        derived holding with average cost
    │   │   ├── allocation.py      target weights + market context they were built with
    │   │   └── portfolio.py       Portfolio: universe, ledger, target history, rebalance(), apply()
    │   ├── market/                what the market says, point-in-time
    │   │   ├── series.py          date-ordered series without look-ahead
    │   │   ├── price_history.py   PricePoint, PriceHistory
    │   │   ├── risk_free.py       RiskFreeRate time series
    │   │   ├── sector_risk.py     sector beta, volatility, rate sensitivity snapshots
    │   │   ├── context.py         MarketContext and its frozen MarketSnapshot
    │   │   ├── provider.py        MarketDataProvider protocol + in-memory implementation
    │   │   └── synthetic.py       deterministic fake prices for tests and the walkthrough
    │   ├── rebalancing/           deciding and planning trades
    │   │   ├── enums.py           Side, TargetMode
    │   │   ├── bands.py           absolute, relative and 5/25 tolerance bands
    │   │   ├── triggers.py        drift, calendar, sector cap, regime change, AnyOf/AllOf
    │   │   ├── policies.py        RebalancePolicy, default_policy(), calendar_policy()
    │   │   ├── costs.py           transaction cost models
    │   │   ├── orders.py          Order, SkippedOrder
    │   │   ├── snapshot.py        PortfolioSnapshot: the rebalancer's input
    │   │   ├── plan.py            RebalancePlan: the rebalancer's output
    │   │   └── rebalancer.py      the algorithm
    │   └── strategies/            choosing the target
    │       ├── risk_model.py      historical covariance, Sharpe ratio, ranking
    │       └── allocation_strategy.py  manual and inverse-volatility allocations
    ├── tests/                     pytest suite (see "Tests")
    └── examples/
        ├── walkthrough.py         invest, drift, regime change, new instrument (synthetic data)
        ├── real_data.py           Yahoo Finance + FRED downloader and cached provider
        └── demo.py                backtest of 8 policies on 2 universes -> results.md
```

---

## Thinking process

Before writing any code I did a short research pass on *why* portfolios are
rebalanced, *how* (common methods) and *how often*. The objective I designed
for is **maximize return for a given, pre-agreed risk, keeping that risk as
stable as possible**, and it must be possible to switch to a new instrument
with a better return/risk ratio.

### 1. Rebalancing and re-optimizing are different things

That distinction drives the architecture:

| | Rebalancing | Re-optimizing |
|---|---|---|
| Question | "Prices moved, how do I get back to my target?" | "Is my target still the right one?" |
| Causes | a stock rallies, a sector runs up together, deposits/withdrawals | a new instrument, a change in the risk-free rate or sector risk, a new risk profile |
| In the code | `Rebalancer` → `RebalancePlan` | `AllocationStrategy` → new `Allocation` |

With a given risk, **rebalancing is what keeps the risk constraint**, and
**return is maximized when the target is chosen**. A portfolio that is never
rebalanced often earns more, but only because it ends up taking more risk.

### 2. Reasons to rebalance, and how each is handled

| Cause | Handled by |
|---|---|
| One stock drifts (META +50%) | `DriftTrigger` with tolerance bands |
| Sector concentration (correlated stocks drifting together) | `SectorLimitTrigger` (sector caps) |
| Deposits, withdrawals, dividends | cash-flow-first rebalancing (buys only) |
| Risk regime change: the risk-free rate or a sector's beta, volatility or rate sensitivity moved | `RegimeChangeTrigger`: asks for re-optimization, does **not** trade |
| A new instrument with a better return/risk ratio | `rank_by_sharpe` to vet it, then a new `Allocation` via `set_allocation` |
| Change of risk profile | `risk_target` + `InverseVolatility(risk_target=...)` |

### 3. What the literature says about methods and frequency

* **Calendar** (monthly, quarterly, yearly): simple, but blind to the market.
* **Threshold / tolerance bands**: trade only when a weight leaves its band.
  Swedroe's **5/25 rule** uses the tighter of ±5pp and ±25% of the target,
  which suits individual stocks because small positions get proportionally
  tighter bands.
* **Hybrid** (monitor often, trade only out of band): Vanguard's *Best
  practices for portfolio rebalancing* (Jaconetti, Kinniry & Zilbering) finds
  no universally optimal frequency. Risk-adjusted returns differ little
  between frequencies, but **costs differ a lot**. Daryanani (2008,
  *Opportunistic Rebalancing*) found that looking often with wide bands
  beat calendar rebalancing.
* **Trade to the band edge, not to the target**: under proportional costs
  the optimal policy is a *no-trade region* around the target, and when a
  weight leaves it you trade back to its edge (Davis & Norman 1990; Leland
  1999). The optimal width grows roughly with the cube root of the cost.
* **Cash flows first**: the cheapest rebalance involves no sales.

**Cheapest:** cash-flow rebalancing, then wide bands traded to the edge.
**Most effective over medium and long horizons (net of costs):** the hybrid.

`default_policy()` is that hybrid: a position must be out of its 5/25 band
and at least a quarter must have passed since the last rebalance; it then
trades to the band edge, cash flows first, and a regime change (checked every
day) blocks trading toward a stale target. The first version monitored and
traded every day; the backtest on real data ([results.md](results.md)) showed
that, with a fixed fee per order, trading to the edge daily causes many small
trades, while the quarterly hybrid had the best cost/benefit.

*The references were cited from memory during the design conversation. Check
the exact titles and years before quoting them.*

### 4. The risk-free rate and sector risk are time series

The risk-free rate is not a constant of the risk model. It moves (roughly 0%
→ 5% for US 3M bills between 2021 and 2023), and Sharpe ratios, CAPM
expected returns, the tangency portfolio and the opportunity cost of cash all
depend on it. The relation between a sector's risk and rates also changes
over time. Both are therefore **recorded over time, point-in-time**:

* `RiskFreeRate`: a series of `RatePoint`s; `rate_at(date)` never looks ahead.
* `SectorRiskSnapshot`: beta to the benchmark, volatility, excess return,
  Sharpe, **rate sensitivity** (return per +100bps) and the rate in force,
  kept in an append-only `SectorRiskHistory`.
* Every `Allocation` records the risk-free rate and sector snapshots it was
  built with. `RegimeChangeTrigger` compares those with today's
  `MarketSnapshot` and flags the target as stale.

---

## Architecture

Files are listed under [Repository structure](#repository-structure).

```
MarketDataProvider ─► Stock.price_history, MarketContext (rf, benchmark, sector risk)
                                   │ as_of(date)
                                   ▼
AllocationStrategy ──► Allocation (weights + context it was built with)
                                   │ set_allocation
                                   ▼
Portfolio (ledger ─► positions, cash) ──snapshot(as_of)──► Rebalancer(policy) ──► RebalancePlan ──► apply
```

### Models

* **Stock**: symbol, full name, GICS sector, currency, price history.
  `current_price()` returns the last available price; `price_at(date)`
  is point-in-time. `returns`, `volatility` and `trend` are derived metrics.
* **Transaction**: an immutable ledger entry (BUY, SELL, DEPOSIT,
  WITHDRAWAL, DIVIDEND, FEE) with its fee.
* **Portfolio**: the ledger, a stock universe, the current target plus the
  history of targets, a cost model and a risk target. Positions (with
  average cost) and cash are **derived by replaying the ledger**, so past
  positions come for free (`positions(as_of=...)`) and the two can never
  disagree. Recording a transaction re-validates the whole ledger (no
  overdraft, no short selling).
* **Allocation**: immutable weights (+ strategic cash) that must sum to 1,
  with a label, a creation time and the market context used to build it.

### Rebalancing algorithm (`lib/portfolio_rebalancer/rebalancing/rebalancer.py`)

1. Value holdings ∪ target at `as_of` prices. Weights are over the total
   value, cash included.
2. Evaluate the trigger. If it does not fire, the plan is empty and carries
   the reasons. On a regime change the plan is also empty, flagged
   `requires_reoptimization`. A target that has not been executed yet always
   triggers.
3. **Cash flows first**: if investing available cash in under-weight symbols
   brings everything back in band, stop there (no sales).
4. Desired weights:
   * `TO_TARGET`: exact target. Used by the calendar baseline and always
     for a newly set target.
   * `TO_BAND_EDGE` (default): out-of-band symbols go to their nearest edge,
     breached sectors go back to target, and the trimming proceeds go to
     in-band under-weights.
5. Orders: whole shares by default (or fractional). Symbols that left the
   target are liquidated. Trades below `min_trade_value`, or whose fee
   exceeds `max_cost_ratio` of their value, are skipped and reported.
6. Sells first. Buys are scaled to the cash available after sells, fees and
   the `cash_buffer`, then topped up share by share with the leftover cash.

---

## Decisions and trade-offs

* **`rebalance()` is pure and returns a plan; `apply()` records it.** The
  plan can be shown to the user, tested, or discarded. `apply` refuses a
  plan if the ledger or the target changed after it was computed
  (`StalePlanError`).
* **The rebalancer gets a frozen `PortfolioSnapshot`, not the `Portfolio`.**
  It works on plain data, so it can be tested in isolation, and the
  rebalancing layer never depends on the domain object that calls it (no
  import cycles).
* **The policy is pluggable** (Strategy pattern): triggers compose with
  `AnyOf` / `AllOf`. The default is
  `AnyOf(AllOf(CalendarTrigger(timedelta(days=91)), DriftTrigger()), RegimeChangeTrigger())`.
* **A new target is executed in full.** Band-edge trading is for *drift*. A
  new target is a strategic decision, so the next rebalance goes all the way
  to it.
* **Rounding toward the target in band-edge mode.** With whole shares,
  rounding down when trading to an edge leaves the position a fraction of a
  share *outside* its band, i.e. still out of tolerance after trading.
  (I found this through the demo; there is a regression test for it.)
* **Never trade back to a stale target.** If the market regime changed
  since the target was built, the plan asks for a new `Allocation` instead.
* **`Decimal` for money, `float` for statistics.** Prices, quantities,
  cash, fees and weights are exact. Volatility, betas and covariances are
  estimates that need square roots.
* **Trend is a metric, not a policy input.** Short-term momentum (3–12
  months) is one reason wide bands work (they let winners run a bit), but
  tilting the bands on trend is a return bet that *adds* risk, and the
  objective is a stable risk.
* **No mean-variance optimizer.** It is the textbook answer for "max return
  subject to σ ≤ target", but it is very sensitive to expected-return
  estimates. It fits behind the same `AllocationStrategy` protocol (ideally
  with shrinkage or Black-Litterman). `InverseVolatility` with a volatility
  target is the robust stand-in implemented here.
* **Point-in-time everywhere.** Prices, rates and sector snapshots are
  looked up "as of" a date, so historical plans are reproducible and free of
  look-ahead bias.

---

## Tests

`.venv/bin/python -m pytest` runs 60 tests, in three files:

| Area | What is verified | File |
|---|---|---|
| Stock | last available price; point-in-time prices (no look-ahead); trend | `test_domain.py` |
| Allocation | weights + cash sum to 1; no shorts; swap to a new instrument; sector weights | `test_domain.py` |
| Ledger | positions, average cost and cash derived from transactions; past positions; overdraft, short selling, unknown symbols and back-dated breaks rejected atomically; dividends | `test_domain.py` |
| Portfolio target | unknown symbols rejected; new instrument joins the universe; foreign currency rejected | `test_domain.py` |
| Bands | absolute, relative, 5/25 (tighter wins), zero target | `test_rebalancing.py` |
| Requirement example (40/60 META/AAPL) | initial buy; META +50% → sell META, buy AAPL back to 40/60; no trades inside the band | `test_rebalancing.py` |
| Default policy | trades to the band edge; whole shares still land inside the band; cash flows used before selling; sector cap forces the sector back; waits for the quarterly check; regime change detected between checks | `test_rebalancing.py` |
| Target changes | new instrument bought and removed one liquidated; new target executed in full; plan for an old target rejected; liquidation ignores minimum trade size | `test_rebalancing.py` |
| Execution constraints | whole vs fractional shares; fees reserved and recorded; tiny and expensive trades skipped with a reason; cash buffer kept | `test_rebalancing.py` |
| Triggers | calendar; hybrid (calendar AND drift) | `test_rebalancing.py` |
| Plan lifecycle | `rebalance()` does not mutate; stale plan rejected; readable summary; empty portfolio | `test_rebalancing.py` |
| Risk-free rate | point-in-time; compounding to a periodic rate | `test_market_and_strategies.py` |
| Sector risk | beta of the benchmark is 1; append-only, point-in-time history; feeds the market snapshot | `test_market_and_strategies.py` |
| Regime change | rf move and sector rate-sensitivity change require re-optimization; small moves ignored; no trades to a stale target | `test_market_and_strategies.py` |
| Strategies | inverse volatility favors the calm stock; the risk target adds cash and hits the target volatility; market context recorded; Sharpe ranking depends on rf; diversification lowers volatility | `test_market_and_strategies.py` |

---

## Next steps

* **Failed or partial executions.** `apply()` assumes every order fills at
  the planned price and fee. A real flow needs order states (submitted,
  partially filled, rejected) and a ledger that records actual fills. After a
  failure, the remaining plan has to be computed again from the real
  position, because a plan with only the sells executed leaves cash idle
  and the weights off target.
* **Market impact: rebalancing gradually.** When an order is large relative
  to the stock's traded volume, executing it at once moves the price
  against us. The plan should cap each order at a fraction of average daily
  volume and spread the rest over several days (with sells and buys kept
  balanced so cash never goes negative). This needs volume data in
  `PriceHistory` and an impact model next to `CostModel`.
* **Recalibrate `RegimeChangeTrigger`.** In the backtest it re-optimized about
  7 times a year, almost always on sector rate sensitivity, which is noisy
  when estimated from daily T-bill changes. Estimate it over a longer window
  (or weekly data), widen its threshold, and add a cooldown between
  re-optimizations.
* Real data sources: implement `MarketDataProvider` (FRED for the risk-free
  rate, a market-data API for prices). See the backtest task below.
* Multiple currencies / FX: rejected explicitly for now.
* Taxes (lot selection, tax-loss harvesting): `Position.avg_cost` is the
  hook for that.
* Better risk models (EWMA, Ledoit-Wolf shrinkage): implement `RiskModel`.

---

## Task for an agent: backtest the policy on real data

Self-contained instructions for a coding agent (or a person). The goal is to
check, on real market data, whether the default policy keeps risk more
stable than buy-and-hold and cheaper rebalancing policies, at a reasonable
cost. Write the results to `results.md`.

**Status: done.** `lib/examples/demo.py` implements it and
[results.md](results.md) has the results. The two main findings feed the
next steps above: with a fixed fee per order, trading exactly to the band
edge produces many small trades (the quarterly hybrid did better and is now
the default), and the regime trigger fires too often on sector rate
sensitivity (still open).

### 1. Data

* **Prices**: daily *adjusted* closes (splits and dividends included), so
  returns are total returns. Use `yfinance` (`auto_adjust=True`), or Stooq
  CSVs if Yahoo is unavailable. Add the package as an optional extra
  (`backtest = ["yfinance"]` in `pyproject.toml`), never as a runtime
  dependency of the library.
* **Risk-free rate**: FRED series `DTB3` (3-month T-bill, percent per year),
  no API key needed:
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3`. Divide by 100,
  and carry the last value forward on days with no data.
* **Benchmark**: `SPY`.
* **Universes**:
  1. The task's example: `META` 40% / `AAPL` 60%.
  2. A diversified one, equal weights: `AAPL`, `MSFT`, `JPM`, `XOM`, `JNJ`,
     `NEE`, `PG` (several GICS sectors).
* **Period**: 2015-01-01 to the last full year available. It includes the
  2020 crash and the 2022 rate hikes, so the regime trigger gets exercised.
* Cache the downloads as CSV under `data/` (git-ignored) so reruns are
  reproducible and do not hit the network.
* Put the downloader in `lib/examples/real_data.py`, as a
  `MarketDataProvider` implementation that reads the cache.

### 2. Simulation (rewrite `lib/examples/demo.py`)

* Start with 100,000 in cash and buy the target on the first day.
  `FixedPlusPercentCost(Decimal("1"), Decimal("0.0005"))`, whole shares.
* Every trading day, call `portfolio.rebalance(policy, as_of=day, market=...)`
  and `apply` the plan at that day's close. Only use the library's
  point-in-time lookups; no data after `day`.
* Record a sector snapshot once a month with `market.record_sector_snapshots`.
* Policies to compare:

| Name | Definition |
|---|---|
| Buy and hold | invest once, never rebalance |
| Calendar monthly / quarterly / yearly | `calendar_policy(every=timedelta(days=30 / 91 / 365))` |
| Threshold to target | `RebalancePolicy(DriftTrigger(AbsoluteBand(Decimal("0.05"))), target_mode=TargetMode.TO_TARGET)` |
| Daily 5/25, band edge (first default) | `RebalancePolicy(DriftTrigger())` |
| Default: hybrid quarterly + 5/25 | `default_policy(detect_regime_change=False)` |
| Default, regime-aware | `default_policy()`; when a plan has `requires_reoptimization`, build a new target with `InverseVolatility(HistoricalRiskModel(stocks), risk_target=Decimal("0.20"))` for the same universe, `set_allocation` it and rebalance again |

### 3. Metrics (per policy and universe)

* CAGR, annualized volatility, Sharpe (excess over the `DTB3` series), max drawdown.
* **Risk stability** (the objective): the standard deviation of the 63-day
  rolling volatility, and the share of days with portfolio volatility within
  ±2pp of the full-period average.
* Mean absolute drift from the target weights.
* Number of trades, turnover (traded value / average portfolio value per
  year), total costs.

### 4. `results.md`

1. Setup: data sources, period, universes, costs, exact command to reproduce.
2. One table of metrics per universe, one row per policy.
3. A chart of portfolio value and of the 63-day rolling volatility per
   policy (PNG under `docs/`, linked from the file).
4. Findings: does the default policy give the most stable risk per unit of
   cost? Where does it lose to the simpler policies?
5. Caveats: survivorship bias (the universes are today's survivors), no
   taxes, fills at the close, Yahoo data quality.

### Acceptance criteria

* `.venv/bin/python -m pytest` still passes. If the backtest exposes a bug
  in the library, fix it and add a test for it.
* `python lib/examples/demo.py` regenerates `results.md` and the charts
  from the cached data.
* No changes to library behavior just to make the results look better.

---

## Use of LLMs

This solution was designed and written with Claude (Anthropic) in Claude
Code, in a single conversation: research on rebalancing strategies → model
design and its review (missing attributes, transactions, costs, the
risk-free rate as a time series) → implementation, code review and tests →
backtest on real data. The conversation is in
[docs/llm-conversation.md](docs/llm-conversation.md), as the task requires.
