"""Statistics are floats: they are estimates that need square roots. Money stays Decimal."""

from __future__ import annotations

import math
from typing import Sequence

TRADING_DAYS_PER_YEAR = 252


def mean(xs: Sequence[float]) -> float:
    if not xs:
        raise ValueError("mean of an empty sequence")
    return sum(xs) / len(xs)


def covariance(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys):
        raise ValueError("series must have the same length")
    if len(xs) < 2:
        raise ValueError("need at least two observations")
    mx, my = mean(xs), mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)


def variance(xs: Sequence[float]) -> float:
    return covariance(xs, xs)


def stdev(xs: Sequence[float]) -> float:
    return math.sqrt(variance(xs))


def regression_beta(ys: Sequence[float], xs: Sequence[float]) -> float:
    var_x = variance(xs)
    return covariance(ys, xs) / var_x if var_x > 0 else 0.0


def annualized_volatility(returns: Sequence[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    return stdev(returns) * math.sqrt(periods_per_year)


def annualized_return(returns: Sequence[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    return mean(returns) * periods_per_year
