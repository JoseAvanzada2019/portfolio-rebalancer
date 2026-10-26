from datetime import date
from decimal import Decimal

import pytest

from conftest import D0, D1, D2, at, stock
from portfolio_rebalancer import Allocation, LedgerError, Portfolio, Sector, Transaction


class TestStock:
    def test_current_price_is_last_available(self, meta):
        assert meta.current_price() == Decimal("150")

    def test_price_at_never_looks_ahead(self, meta):
        assert meta.price_at(date(2025, 3, 1)) == Decimal("100")  # last known before D2
        with pytest.raises(LookupError):
            meta.price_at(date(2024, 12, 31))

    def test_update_price_and_trend(self, meta):
        meta.update_price("165", date(2025, 6, 3))
        assert meta.current_price() == Decimal("165")
        assert meta.trend(window=2) == pytest.approx(0.65)


class TestAllocation:
    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError, match="sum to 1"):
            Allocation.from_percentages({"META": 40, "AAPL": 50})

    def test_no_short_weights(self):
        with pytest.raises(ValueError):
            Allocation({"META": Decimal("1.2"), "AAPL": Decimal("-0.2")})

    def test_cash_weight_counts_towards_total(self):
        allocation = Allocation({"META": Decimal("0.5")}, cash_weight=Decimal("0.5"))
        assert allocation.weight("META") == Decimal("0.5")
        assert allocation.weight("AAPL") == 0

    def test_swap_moves_weight_to_new_instrument(self):
        allocation = Allocation.from_percentages({"META": 40, "AAPL": 60}).swap("META", "MSFT")
        assert dict(allocation.weights) == {"AAPL": Decimal("0.6"), "MSFT": Decimal("0.4")}

    def test_sector_weights(self, meta, aapl):
        allocation = Allocation.from_percentages({"META": 40, "AAPL": 60})
        weights = allocation.sector_weights({"META": meta.sector, "AAPL": aapl.sector})
        assert weights[Sector.INFORMATION_TECHNOLOGY] == Decimal("0.6")


class TestLedger:
    def test_positions_and_cash_are_derived_from_transactions(self, funded):
        funded.record(Transaction.buy("META", 10, 100, fee=5, timestamp=at(D1)))
        funded.record(Transaction.buy("META", 10, 110, fee=5, timestamp=at(D1)))
        position = funded.positions()["META"]
        assert position.quantity == 20
        assert position.avg_cost == Decimal("105.5")  # (1000 + 1100 + 10) / 20
        assert funded.cash() == Decimal("10000") - 2110

    def test_past_positions(self, funded):
        funded.record(Transaction.buy("AAPL", 5, 200, timestamp=at(D1)))
        assert funded.positions(as_of=D0) == {}
        assert funded.positions(as_of=D1)["AAPL"].quantity == 5

    def test_rejects_overdraft_and_short_selling(self, funded):
        with pytest.raises(LedgerError, match="negative cash"):
            funded.record(Transaction.buy("AAPL", 100, 200, timestamp=at(D1)))
        with pytest.raises(LedgerError, match="cannot sell"):
            funded.record(Transaction.sell("AAPL", 1, 200, timestamp=at(D1)))
        assert len(funded.transactions) == 1

    def test_backdated_transaction_that_breaks_history_is_rejected(self, funded):
        funded.withdraw(9_000, at(D2))
        with pytest.raises(LedgerError):
            funded.record(Transaction.buy("AAPL", 10, 200, timestamp=at(D1)))

    def test_unknown_symbol_rejected(self, funded):
        with pytest.raises(KeyError):
            funded.record(Transaction.buy("TSLA", 1, 10, timestamp=at(D1)))

    def test_dividend_adds_cash(self, funded):
        funded.record(Transaction.dividend("AAPL", 25, at(D1)))
        assert funded.cash() == Decimal("10025")

    def test_weights_drift_and_sectors(self, funded):
        funded.record(Transaction.buy("META", 40, 100, timestamp=at(D1)))
        funded.record(Transaction.buy("AAPL", 30, 200, timestamp=at(D1)))
        assert funded.current_weights(D1) == {"AAPL": Decimal("0.6"), "META": Decimal("0.4")}
        # META +50% by D2: 6000 / 12000
        assert funded.current_weights(D2)["META"] == Decimal("0.5")
        assert funded.drift(D2)["META"] == Decimal("0.1")
        assert funded.sector_weights(D2)[Sector.COMMUNICATION_SERVICES] == Decimal("0.5")


class TestPortfolioAllocation:
    def test_allocation_must_reference_known_stocks(self, funded):
        with pytest.raises(ValueError, match="unknown symbols"):
            funded.set_allocation(Allocation.from_percentages({"META": 50, "MSFT": 50}))

    def test_new_instrument_joins_universe_and_history(self, funded):
        msft = stock("MSFT", {D0: 300})
        funded.set_allocation(Allocation.from_percentages({"META": 40, "MSFT": 60}), new_stocks=[msft])
        assert "MSFT" in funded.stocks
        assert len(funded.allocation_history) == 2

    def test_foreign_currency_rejected(self, meta):
        meta.currency = "EUR"
        with pytest.raises(ValueError, match="FX"):
            Portfolio("x", Allocation.from_percentages({"META": 100}), [meta])
