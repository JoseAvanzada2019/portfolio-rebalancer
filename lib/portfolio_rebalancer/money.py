from __future__ import annotations

from decimal import Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
BPS_PER_UNIT = Decimal("10000")


def to_decimal(value: Decimal | float | int | str) -> Decimal:
    """Floats go through ``str`` so 0.1 becomes Decimal("0.1"), not its binary approximation."""
    return value if isinstance(value, Decimal) else Decimal(str(value))
