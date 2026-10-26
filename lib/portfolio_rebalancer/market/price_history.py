from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from portfolio_rebalancer.market.series import DatedSeries
from portfolio_rebalancer.money import to_decimal


@dataclass(frozen=True)
class PricePoint:
    date: date
    close: Decimal

    def __post_init__(self) -> None:
        close = to_decimal(self.close)
        if close <= 0:
            raise ValueError(f"price must be positive, got {close}")
        object.__setattr__(self, "close", close)


class PriceHistory(DatedSeries[Decimal]):
    def __init__(self, points: Iterable[PricePoint] = ()) -> None:
        super().__init__((p.date, p.close) for p in points)

    def add(self, point: PricePoint) -> None:
        self._put(point.date, point.close)

    def last(self) -> PricePoint:
        if not self._dates:
            raise LookupError("price history is empty")
        return PricePoint(self._dates[-1], self._values[-1])

    def at(self, on: date) -> PricePoint:
        i = self._index_at(on)
        return PricePoint(self._dates[i], self._values[i])

    def points(self) -> tuple[PricePoint, ...]:
        return tuple(PricePoint(d, v) for d, v in zip(self._dates, self._values))

    def dates_until(self, on: date | None = None, last_n: int | None = None) -> list[date]:
        return [d for d, _ in self._items_until(on, last_n)]

    def returns(self, window: int | None = None, as_of: date | None = None) -> list[float]:
        """Simple returns of the last ``window`` periods up to ``as_of``."""
        last_n = None if window is None else window + 1
        closes = [v for _, v in self._items_until(as_of, last_n)]
        return [float(curr / prev - 1) for prev, curr in zip(closes, closes[1:])]
