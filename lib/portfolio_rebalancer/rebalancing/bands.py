"""Tolerance bands map a target weight to its allowed (low, high) range. A zero target only allows zero."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from portfolio_rebalancer.money import ONE, ZERO, to_decimal


class Band(Protocol):
    def bounds(self, target: Decimal) -> tuple[Decimal, Decimal]: ...


def _around(target: Decimal, width: Decimal) -> tuple[Decimal, Decimal]:
    if target == 0:
        return ZERO, ZERO
    return max(ZERO, target - width), min(ONE, target + width)


@dataclass(frozen=True)
class AbsoluteBand:
    width: Decimal = Decimal("0.05")

    def bounds(self, target: Decimal) -> tuple[Decimal, Decimal]:
        return _around(target, to_decimal(self.width))


@dataclass(frozen=True)
class RelativeBand:
    percent: Decimal = Decimal("0.25")

    def bounds(self, target: Decimal) -> tuple[Decimal, Decimal]:
        return _around(target, target * to_decimal(self.percent))


@dataclass(frozen=True)
class FiveTwentyFive:
    """Swedroe's 5/25 rule: the tighter of ±5pp and ±25% of the target.

    A 40% target gets ±5pp; an 8% target gets ±2pp, so small positions cannot
    double unnoticed.
    """

    absolute: Decimal = Decimal("0.05")
    relative: Decimal = Decimal("0.25")

    def bounds(self, target: Decimal) -> tuple[Decimal, Decimal]:
        return _around(target, min(to_decimal(self.absolute), target * to_decimal(self.relative)))
