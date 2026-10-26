from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from portfolio_rebalancer import Allocation, Portfolio, PriceHistory, PricePoint, Sector, Stock

D0 = date(2025, 1, 2)  # funding day
D1 = date(2025, 1, 3)  # first rebalance
D2 = date(2025, 6, 2)  # after prices moved


def at(on: date) -> datetime:
    return datetime(on.year, on.month, on.day, 10, tzinfo=timezone.utc)


def stock(symbol: str, prices: dict, sector: Sector = Sector.INFORMATION_TECHNOLOGY, name: str | None = None) -> Stock:
    history = PriceHistory(PricePoint(d, Decimal(str(p))) for d, p in prices.items())
    return Stock(symbol, name or symbol, sector, price_history=history)


@pytest.fixture
def meta() -> Stock:
    return stock("META", {D0: 100, D1: 100, D2: 150}, Sector.COMMUNICATION_SERVICES, "Meta Platforms, Inc.")


@pytest.fixture
def aapl() -> Stock:
    return stock("AAPL", {D0: 200, D1: 200, D2: 200}, name="Apple Inc.")


@pytest.fixture
def funded(meta, aapl) -> Portfolio:
    """40/60 META/AAPL with 10,000 in cash and nothing invested yet."""
    portfolio = Portfolio("test", Allocation.from_percentages({"META": 40, "AAPL": 60}), [meta, aapl])
    portfolio.deposit(10_000, at(D0))
    return portfolio
