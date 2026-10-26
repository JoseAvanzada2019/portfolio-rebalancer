from __future__ import annotations

from typing import Iterable, Mapping, Protocol

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.domain.stock import Stock
from portfolio_rebalancer.market.context import MarketContext
from portfolio_rebalancer.market.price_history import PriceHistory, PricePoint
from portfolio_rebalancer.market.risk_free import RatePoint, RiskFreeRate


class MarketDataProvider(Protocol):
    """Seam for real sources, e.g. FRED for the risk-free rate and a market-data API for prices."""

    def price_history(self, symbol: str) -> PriceHistory: ...

    def risk_free_rate(self) -> RiskFreeRate: ...

    def benchmark(self) -> PriceHistory: ...


class InMemoryMarketDataProvider:
    def __init__(
        self,
        prices: Mapping[str, Iterable[PricePoint]],
        risk_free: Iterable[RatePoint],
        benchmark: Iterable[PricePoint],
        risk_free_source: str = "in-memory",
        risk_free_tenor: str = "3M",
    ) -> None:
        self._prices = {symbol: list(points) for symbol, points in prices.items()}
        self._risk_free = list(risk_free)
        self._benchmark = list(benchmark)
        self._risk_free_source = risk_free_source
        self._risk_free_tenor = risk_free_tenor

    def price_history(self, symbol: str) -> PriceHistory:
        return PriceHistory(self._prices[symbol])

    def risk_free_rate(self) -> RiskFreeRate:
        return RiskFreeRate(self._risk_free_source, self._risk_free_tenor, self._risk_free)

    def benchmark(self) -> PriceHistory:
        return PriceHistory(self._benchmark)

    def stock(self, symbol: str, name: str, sector: Sector, currency: str = "USD") -> Stock:
        return Stock(symbol, name, sector, currency, self.price_history(symbol))

    def market_context(self) -> MarketContext:
        return MarketContext(self.risk_free_rate(), self.benchmark())
