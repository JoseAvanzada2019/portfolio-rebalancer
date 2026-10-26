from enum import Enum


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class TargetMode(Enum):
    """Where an out-of-tolerance portfolio is traded to."""

    TO_TARGET = "to_target"
    TO_BAND_EDGE = "to_band_edge"
