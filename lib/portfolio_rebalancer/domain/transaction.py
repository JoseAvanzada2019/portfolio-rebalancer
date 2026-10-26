from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from portfolio_rebalancer.domain.enums import TransactionType
from portfolio_rebalancer.money import ZERO, to_decimal

_TRADES = {TransactionType.BUY, TransactionType.SELL}
_CASH_SIGN = {
    TransactionType.BUY: -1,
    TransactionType.SELL: 1,
    TransactionType.DEPOSIT: 1,
    TransactionType.DIVIDEND: 1,
    TransactionType.WITHDRAWAL: -1,
    TransactionType.FEE: -1,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Transaction:
    """Immutable ledger entry. Trades use quantity and price; cash movements use amount.

    Naive timestamps are taken as UTC so the ledger can always be ordered.
    """

    type: TransactionType
    timestamp: datetime = field(default_factory=_now)
    symbol: str | None = None
    quantity: Decimal = ZERO
    price: Decimal = ZERO
    amount: Decimal = ZERO
    fee: Decimal = ZERO
    note: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        for name in ("quantity", "price", "amount", "fee"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=timezone.utc))
        self._validate()

    def _validate(self) -> None:
        if self.fee < 0:
            raise ValueError("fee cannot be negative")
        if self.type in _TRADES:
            if not self.symbol or self.quantity <= 0 or self.price <= 0:
                raise ValueError(f"{self.type.value} needs a symbol, positive quantity and price")
            return
        if self.amount <= 0:
            raise ValueError(f"{self.type.value} needs a positive amount")
        if self.type is TransactionType.DIVIDEND and not self.symbol:
            raise ValueError("DIVIDEND needs the paying symbol")

    @property
    def gross_value(self) -> Decimal:
        return self.quantity * self.price if self.type in _TRADES else self.amount

    def cash_delta(self) -> Decimal:
        return _CASH_SIGN[self.type] * self.gross_value - self.fee

    @classmethod
    def buy(cls, symbol: str, quantity, price, fee=ZERO, timestamp: datetime | None = None, note: str = "") -> Transaction:
        return cls(TransactionType.BUY, timestamp or _now(), symbol, quantity, price, fee=fee, note=note)

    @classmethod
    def sell(cls, symbol: str, quantity, price, fee=ZERO, timestamp: datetime | None = None, note: str = "") -> Transaction:
        return cls(TransactionType.SELL, timestamp or _now(), symbol, quantity, price, fee=fee, note=note)

    @classmethod
    def deposit(cls, amount, timestamp: datetime | None = None, note: str = "") -> Transaction:
        return cls(TransactionType.DEPOSIT, timestamp or _now(), amount=amount, note=note)

    @classmethod
    def withdrawal(cls, amount, timestamp: datetime | None = None, note: str = "") -> Transaction:
        return cls(TransactionType.WITHDRAWAL, timestamp or _now(), amount=amount, note=note)

    @classmethod
    def dividend(cls, symbol: str, amount, timestamp: datetime | None = None, note: str = "") -> Transaction:
        return cls(TransactionType.DIVIDEND, timestamp or _now(), symbol, amount=amount, note=note)
