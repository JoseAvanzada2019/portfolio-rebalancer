from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date
from typing import Generic, Iterable, TypeVar

T = TypeVar("T")


class DatedSeries(Generic[T]):
    """Date-ordered values with point-in-time lookups: nothing after the requested date is ever returned."""

    def __init__(self, items: Iterable[tuple[date, T]] = ()) -> None:
        self._dates: list[date] = []
        self._values: list[T] = []
        for on, value in items:
            self._put(on, value)

    def __len__(self) -> int:
        return len(self._dates)

    def dates(self) -> tuple[date, ...]:
        return tuple(self._dates)

    def _put(self, on: date, value: T) -> None:
        """A second value for the same date replaces the first (a correction)."""
        i = bisect_left(self._dates, on)
        if i < len(self._dates) and self._dates[i] == on:
            self._values[i] = value
        else:
            self._dates.insert(i, on)
            self._values.insert(i, value)

    def _index_at(self, on: date) -> int:
        i = bisect_right(self._dates, on) - 1
        if i < 0:
            raise LookupError(f"no data on or before {on}")
        return i

    def _items_until(self, on: date | None, last_n: int | None = None) -> list[tuple[date, T]]:
        end = len(self._dates) if on is None else bisect_right(self._dates, on)
        start = 0 if last_n is None else max(0, end - last_n)
        return list(zip(self._dates[start:end], self._values[start:end]))
