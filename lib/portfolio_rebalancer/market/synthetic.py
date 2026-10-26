"""Deterministic fake market data for tests and the demo."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from decimal import Decimal

from portfolio_rebalancer.market.price_history import PricePoint
from portfolio_rebalancer.stats import TRADING_DAYS_PER_YEAR


def business_days(start: date, count: int) -> list[date]:
    days, current = [], start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def synthetic_prices(
    start: date,
    days: int,
    start_price: float,
    annual_drift: float,
    annual_volatility: float,
    seed: int,
) -> list[PricePoint]:
    """Geometric Brownian motion on business days."""
    rng = random.Random(seed)
    dt = 1 / TRADING_DAYS_PER_YEAR
    drift = (annual_drift - annual_volatility**2 / 2) * dt
    shock_scale = annual_volatility * math.sqrt(dt)
    price, points = start_price, []
    for i, on in enumerate(business_days(start, days)):
        if i:
            price *= math.exp(drift + shock_scale * rng.gauss(0, 1))
        points.append(PricePoint(on, Decimal(f"{price:.2f}")))
    return points
