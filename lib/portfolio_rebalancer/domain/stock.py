from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from portfolio_rebalancer.domain.enums import Sector
from portfolio_rebalancer.market.price_history import PriceHistory, PricePoint
from portfolio_rebalancer.money import to_decimal
from portfolio_rebalancer.stats import TRADING_DAYS_PER_YEAR, annualized_volatility


@dataclass
class Stock:
    symbol: str
    name: str
    sector: Sector
    currency: str = "USD"
    price_history: PriceHistory = field(default_factory=PriceHistory, repr=False)

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")

    def current_price(self) -> Decimal:
        return self.price_history.last().close

    def price_at(self, on: date) -> Decimal:
        return self.price_history.at(on).close

    def update_price(self, price: Decimal | float | str, on: date) -> None:
        self.price_history.add(PricePoint(on, to_decimal(price)))

    def returns(self, window: int | None = None, as_of: date | None = None) -> list[float]:
        return self.price_history.returns(window, as_of)

    def volatility(self, window: int = TRADING_DAYS_PER_YEAR, as_of: date | None = None) -> float:
        return annualized_volatility(self.returns(window, as_of))

    def trend(self, window: int = 63, as_of: date | None = None) -> float:
        """Cumulative return over the last ``window`` days (63 ≈ a quarter)."""
        growth = 1.0
        for r in self.returns(window, as_of):
            growth *= 1 + r
        return growth - 1
