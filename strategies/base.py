"""
Abstract base every strategy subclasses. This file should never need to
change when a new strategy is added — that's the whole point of the
registry pattern in registry.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from core.signal import Signal


class Strategy(ABC):
    name: str = "unnamed"
    description: str = ""
    default_timeframe: str = "M15"

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Optional[Signal]:
        """
        df: OHLCV candles up to and including the most recently CLOSED bar
            (never a partially-formed current bar — no lookahead).
        Return a Signal for a new setup, or None if there's nothing to do
        right now. Do not set .rr/.status — validation.py owns that.
        """
        raise NotImplementedError
