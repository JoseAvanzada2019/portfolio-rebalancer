from __future__ import annotations

from bisect import insort
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from operator import attrgetter
from typing import Iterable, Mapping

from portfolio_rebalancer.domain.enums import TransactionType
from portfolio_rebalancer.domain.exceptions import LedgerError
from portfolio_rebalancer.domain.position import Position
from portfolio_rebalancer.domain.transaction import Transaction
from portfolio_rebalancer.money import ZERO

_by_time = attrgetter("timestamp")


@dataclass(frozen=True)
class LedgerState:
    cash: Decimal
    positions: Mapping[str, Position]


class Ledger:
    """Time-ordered movements. Positions and cash are never stored: they are replayed from here.

    Every write replays the whole history, so a back-dated movement that would
    overdraw cash or sell shares not yet held is rejected.
    """

    def __init__(self) -> None:
        self._transactions: list[Transaction] = []

    def __len__(self) -> int:
        return len(self._transactions)

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        return tuple(self._transactions)

    def record(self, *transactions: Transaction) -> None:
        """Record all movements or none."""
        candidate = list(self._transactions)
        for tx in transactions:
            insort(candidate, tx, key=_by_time)
        _replay(candidate)
        self._transactions = candidate

    def state(self, as_of: date | None = None) -> LedgerState:
        return _replay(self._transactions, as_of)


def _replay(transactions: Iterable[Transaction], until: date | None = None) -> LedgerState:
    cash = ZERO
    quantities: dict[str, Decimal] = {}
    avg_costs: dict[str, Decimal] = {}
    for tx in transactions:
        if until is not None and tx.timestamp.date() > until:
            break
        if tx.type is TransactionType.BUY:
            held = quantities.get(tx.symbol, ZERO)
            quantities[tx.symbol] = held + tx.quantity
            avg_costs[tx.symbol] = (held * avg_costs.get(tx.symbol, ZERO) + tx.gross_value + tx.fee) / quantities[tx.symbol]
        elif tx.type is TransactionType.SELL:
            held = quantities.get(tx.symbol, ZERO)
            if tx.quantity > held:
                raise LedgerError(f"cannot sell {tx.quantity} {tx.symbol}: only {held} held at {tx.timestamp}")
            quantities[tx.symbol] = held - tx.quantity
            if quantities[tx.symbol] == 0:
                del quantities[tx.symbol], avg_costs[tx.symbol]
        cash += tx.cash_delta()
        if cash < 0:
            raise LedgerError(f"{tx.type.value} at {tx.timestamp} would leave negative cash ({cash})")
    return LedgerState(cash, {s: Position(s, q, avg_costs[s]) for s, q in quantities.items()})
