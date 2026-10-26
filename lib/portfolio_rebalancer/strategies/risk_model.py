from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping, Protocol, Sequence

from portfolio_rebalancer.domain.stock import Stock
from portfolio_rebalancer.stats import (
    TRADING_DAYS_PER_YEAR,
    annualized_return,
    annualized_volatility,
    covariance,
)


class RiskModel(Protocol):
    def volatility(self, symbol: str, as_of: date | None = None) -> float: ...

    def covariance_matrix(self, symbols: Sequence[str], as_of: date | None = None) -> list[list[float]]: ...

    def portfolio_volatility(self, weights: Mapping[str, Decimal], as_of: date | None = None) -> float: ...


class HistoricalRiskModel:
    """Annualized sample covariance of daily returns over a rolling window.

    Simple on purpose; EWMA or Ledoit-Wolf shrinkage would plug in behind ``RiskModel``.
    """

    def __init__(self, stocks: Iterable[Stock], window: int = TRADING_DAYS_PER_YEAR) -> None:
        self._stocks = {s.symbol: s for s in stocks}
        self.window = window

    def volatility(self, symbol: str, as_of: date | None = None) -> float:
        return annualized_volatility(self._aligned_returns([symbol], as_of)[0])

    def covariance_matrix(self, symbols: Sequence[str], as_of: date | None = None) -> list[list[float]]:
        returns = self._aligned_returns(symbols, as_of)
        return [[covariance(a, b) * TRADING_DAYS_PER_YEAR for b in returns] for a in returns]

    def portfolio_volatility(self, weights: Mapping[str, Decimal], as_of: date | None = None) -> float:
        """sqrt(wᵀ Σ w); cash carries no volatility, so only stock weights count."""
        symbols = [s for s, w in weights.items() if w > 0]
        if not symbols:
            return 0.0
        w = [float(weights[s]) for s in symbols]
        cov = self.covariance_matrix(symbols, as_of)
        variance = sum(w[i] * w[j] * cov[i][j] for i in range(len(w)) for j in range(len(w)))
        return math.sqrt(max(variance, 0.0))

    def _aligned_returns(self, symbols: Sequence[str], as_of: date | None) -> list[list[float]]:
        series = [self._stocks[s].returns(self.window, as_of) for s in symbols]
        n = min(len(r) for r in series)
        if n < 2:
            raise ValueError("not enough price history to estimate risk")
        return [r[-n:] for r in series]


def sharpe_ratio(stock: Stock, risk_free_rate: Decimal, as_of: date | None = None, window: int = TRADING_DAYS_PER_YEAR) -> float:
    returns = stock.returns(window, as_of)
    volatility = annualized_volatility(returns)
    if volatility == 0:
        return 0.0
    return (annualized_return(returns) - float(risk_free_rate)) / volatility


def rank_by_sharpe(
    stocks: Iterable[Stock], risk_free_rate: Decimal, as_of: date | None = None, window: int = TRADING_DAYS_PER_YEAR
) -> list[tuple[str, float]]:
    """Best first. Historical Sharpe is a noisy estimate: a screen for new instruments, not proof."""
    ranked = [(s.symbol, sharpe_ratio(s, risk_free_rate, as_of, window)) for s in stocks]
    return sorted(ranked, key=lambda pair: pair[1], reverse=True)
