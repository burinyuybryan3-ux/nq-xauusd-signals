"""
DataProvider abstraction. Every provider — MT5, OANDA, Dukascopy, Twelve
Data, yfinance — implements this same interface, so the strategy engine,
scheduler and backtester never know which one is underneath.

Standard output: a pandas DataFrame indexed by UTC timestamp with columns
open, high, low, close, volume. Always ascending by time.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


class ProviderUnavailable(Exception):
    """Raised when a provider cannot serve data right now (terminal not
    running, missing API key, rate-limited with no cache, network error).
    The manager catches this and falls through to the next provider."""


@dataclass
class PriceQuote:
    symbol: str
    price: float
    is_live: bool          # True = real-time feed, False = delayed
    provider: str
    timeframe_note: str = ""  # e.g. "delayed ~15m" for display


class DataProvider(ABC):
    name: str = "base"
    is_live: bool = False   # override True for real-time providers (MT5, OANDA)

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, count: int = 500,
                     start=None, end=None) -> pd.DataFrame:
        """Return an OHLCV DataFrame, UTC-indexed, ascending, columns
        open/high/low/close/volume. `symbol` is the *logical* symbol
        (e.g. 'NQ', 'XAUUSD') — the provider maps it to its own ticker."""
        raise NotImplementedError

    @abstractmethod
    def get_live_price(self, symbol: str) -> PriceQuote:
        raise NotImplementedError

    def map_symbol(self, symbol: str, symbol_map: dict) -> str:
        """Look up this provider's ticker for a logical symbol from config.yaml."""
        entry = symbol_map.get(symbol)
        if not entry or self.name not in entry:
            raise ProviderUnavailable(
                f"No symbol mapping for '{symbol}' on provider '{self.name}'"
            )
        return entry[self.name]

    @staticmethod
    def normalize(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure UTC index, correct column order/names, ascending sort."""
        df = df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataProvider output must be indexed by timestamp")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        return df
