"""Real market data: Yahoo Finance adjusted closes and FRED's 3-month T-bill, cached as CSV under data/."""

from __future__ import annotations

import csv
import io
import urllib.request
from datetime import date
from decimal import Decimal
from pathlib import Path

from portfolio_rebalancer import (
    MarketContext,
    PriceHistory,
    PricePoint,
    RatePoint,
    RiskFreeRate,
    Sector,
    Stock,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
RISK_FREE_SERIES = "DTB3"
FRED_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={RISK_FREE_SERIES}"


def download(symbols: list[str], start: date, end: date, refresh: bool = False) -> None:
    """Fill the cache; files already present are kept unless ``refresh``."""
    import yfinance as yf

    DATA_DIR.mkdir(exist_ok=True)
    for symbol in symbols:
        path = DATA_DIR / f"{symbol}.csv"
        if path.exists() and not refresh:
            continue
        closes = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(), auto_adjust=True)["Close"]
        if closes.empty:
            raise RuntimeError(f"no data downloaded for {symbol}")
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "close"])
            writer.writerows((ts.date().isoformat(), f"{close:.4f}") for ts, close in closes.items())

    path = DATA_DIR / f"{RISK_FREE_SERIES}.csv"
    if refresh or not path.exists():
        with urllib.request.urlopen(FRED_URL, timeout=30) as response:
            path.write_text(response.read().decode())


class CachedMarketDataProvider:
    """``MarketDataProvider`` over the CSV cache."""

    def __init__(self, data_dir: Path = DATA_DIR, benchmark_symbol: str = "SPY") -> None:
        self.data_dir = data_dir
        self.benchmark_symbol = benchmark_symbol

    def price_history(self, symbol: str) -> PriceHistory:
        with (self.data_dir / f"{symbol}.csv").open() as f:
            return PriceHistory(PricePoint(date.fromisoformat(row["date"]), Decimal(row["close"])) for row in csv.DictReader(f))

    def risk_free_rate(self) -> RiskFreeRate:
        text = (self.data_dir / f"{RISK_FREE_SERIES}.csv").read_text()
        points = [
            RatePoint(date.fromisoformat(row["observation_date"]), Decimal(row[RISK_FREE_SERIES]) / 100)
            for row in csv.DictReader(io.StringIO(text))
            if row[RISK_FREE_SERIES] not in ("", ".")
        ]
        return RiskFreeRate("US 3M T-Bill (FRED DTB3)", "3M", points)

    def benchmark(self) -> PriceHistory:
        return self.price_history(self.benchmark_symbol)

    def stock(self, symbol: str, name: str, sector: Sector) -> Stock:
        return Stock(symbol, name, sector, "USD", self.price_history(symbol))

    def market_context(self) -> MarketContext:
        return MarketContext(self.risk_free_rate(), self.benchmark())
