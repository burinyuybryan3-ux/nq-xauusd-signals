"""
Dukascopy provider — primary HISTORICAL source for backtesting (free,
high-quality tick/candle history for XAUUSD and indices).

Dukascopy has no official REST API with an API key; free historical access
means pulling their public tick-data feed (.bi5 files) or the "JForex" JSON
chart endpoint used by community tooling (e.g. dukascopy-node). Both are
undocumented/unofficial and can change without notice — that's the tradeoff
for it being free. This implementation:

  - Tries the JSON chart endpoint for OHLC candles.
  - Raises ProviderUnavailable on any failure (wrong response shape, network
    error, symbol not found) so DataProviderManager falls through to
    Twelve Data, then yfinance, without the caller needing to know why.

If this endpoint breaks in the future, the fix is contained to this one file
— everything else in the app depends only on the DataProvider interface.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests

from data_providers.base import DataProvider, ProviderUnavailable, PriceQuote

_TIMEFRAME_MS = {
    "M1": 60_000, "M5": 300_000, "M15": 900_000, "M30": 1_800_000,
    "H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000,
}

_CHART_URL = "https://freeserv.dukascopy.com/2.0/index.php"


class DukascopyProvider(DataProvider):
    name = "dukascopy"
    is_live = False

    def __init__(self, symbol_map: dict):
        self.symbol_map = symbol_map

    def _ticker(self, symbol: str) -> str:
        return self.map_symbol(symbol, self.symbol_map)

    def get_candles(self, symbol: str, timeframe: str, count: int = 500,
                     start=None, end=None) -> pd.DataFrame:
        ticker = self._ticker(symbol)
        if timeframe not in _TIMEFRAME_MS:
            raise ProviderUnavailable(f"Unsupported timeframe for Dukascopy: {timeframe}")

        end = end or datetime.now(timezone.utc)
        start = start or (end - pd.Timedelta(milliseconds=_TIMEFRAME_MS[timeframe] * count))

        params = {
            "path": "chart/json3",
            "instrument": ticker,
            "offer_side": "B",
            "interval": _dukascopy_interval(timeframe),
            "splits": "true",
            "stocks": "false",
            "start_time": int(start.timestamp() * 1000),
            "end_time": int(end.timestamp() * 1000),
        }

        try:
            resp = requests.get(_CHART_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise ProviderUnavailable(f"Dukascopy fetch failed for '{ticker}': {e}") from e

        candles = data.get("candles") if isinstance(data, dict) else None
        if not candles:
            raise ProviderUnavailable(f"Dukascopy returned no candles for '{ticker}'")

        df = pd.DataFrame(candles, columns=["ts", "open", "close", "low", "high", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts")
        return self.normalize(df).tail(count)

    def get_live_price(self, symbol: str) -> PriceQuote:
        # Dukascopy free feed is historical/EOD-oriented; don't pretend it's live.
        raise ProviderUnavailable("Dukascopy provider is historical-only, no live price")


def _dukascopy_interval(timeframe: str) -> str:
    return {
        "M1": "1", "M5": "5", "M15": "15", "M30": "30",
        "H1": "60", "H4": "240", "D1": "1D",
    }[timeframe]
