class LedgerError(ValueError):
    """A movement would leave the portfolio with negative cash or a short position."""


class StalePlanError(RuntimeError):
    """The ledger or the target allocation changed after the plan was computed."""
