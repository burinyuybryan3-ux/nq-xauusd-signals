"""
yfinance provider — last-resort fallback, delayed data, no API key needed.
Used for: (a) historical backtest fallback, (b) local smoke-testing the
pipeline when MT5/OANDA aren't configured yet.

Never presented as live — is_live is hard-set False.
"""
from __future__ import annotations

import pandas as pd

from data_providers.base import DataProvider, ProviderUnavailable, PriceQuote

_TIMEFRAME_MAP = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "1h",  # yfinance has no native 4h; caller resamples if needed
    "D1": "1d",
}

_PERIOD_FOR_INTERVAL = {
    "1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d", "1d": "10y",
}


class YFinanceProvider(DataProvider):
    name = "yfinance"
    is_live = False

    def __init__(self, symbol_map: dict):
        self.symbol_map = symbol_map

    def _ticker(self, symbol: str) -> str:
        return self.map_symbol(symbol, self.symbol_map)

    def get_candles(self, symbol: str, timeframe: str, count: int = 500,
                     start=None, end=None) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as e:
            raise ProviderUnavailable("yfinance package not installed") from e

        ticker = self._ticker(symbol)
        interval = _TIMEFRAME_MAP.get(timeframe, "1h")
        kwargs = {"interval": interval}
        if start or end:
            kwargs["start"] = start
            kwargs["end"] = end
        else:
            kwargs["period"] = _PERIOD_FOR_INTERVAL.get(interval, "60d")

        try:
            raw = yf.download(ticker, progress=False, auto_adjust=False, **kwargs)
        except Exception as e:
            raise ProviderUnavailable(f"yfinance download failed for {ticker}: {e}") from e

        if raw is None or raw.empty:
            raise ProviderUnavailable(f"yfinance returned no data for {ticker}")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df = self.normalize(df)
        if timeframe == "H4":
            df = (
                df.resample("4h")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                .dropna()
            )
        return df.tail(count)

    def get_live_price(self, symbol: str) -> PriceQuote:
        df = self.get_candles(symbol, "M1", count=1)
        return PriceQuote(
            symbol=symbol,
            price=float(df["close"].iloc[-1]),
            is_live=False,
            provider=self.name,
            timeframe_note="delayed (yfinance)",
        )
