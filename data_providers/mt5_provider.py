"""
MetaTrader 5 provider — the default LIVE local feed.

Reads real-time candles/ticks from a running MT5 terminal via the
`MetaTrader5` Python package. Requires:
  - MetaTrader 5 terminal installed and running on this machine (Windows only)
  - Logged into a broker/prop account (e.g. your FundedNext demo/live)
  - `pip install MetaTrader5`

This CANNOT run on a cloud free tier — there is no terminal to attach to.
Use OANDAProvider there instead (see config.yaml `environment:`).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from data_providers.base import DataProvider, ProviderUnavailable, PriceQuote

_TIMEFRAME_ATTR = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class MT5Provider(DataProvider):
    name = "mt5"
    is_live = True

    def __init__(self, symbol_map: dict):
        self.symbol_map = symbol_map
        self._mt5 = None
        self._initialized = False

    def _ensure_init(self):
        if self._initialized:
            return
        try:
            import MetaTrader5 as mt5
        except ImportError as e:
            raise ProviderUnavailable(
                "MetaTrader5 package not installed (pip install MetaTrader5) — Windows only"
            ) from e

        if not mt5.initialize():
            code = mt5.last_error()
            raise ProviderUnavailable(
                f"MT5 initialize() failed — is the terminal running and logged in? {code}"
            )
        self._mt5 = mt5
        self._initialized = True

    def _ticker(self, symbol: str) -> str:
        return self.map_symbol(symbol, self.symbol_map)

    def get_candles(self, symbol: str, timeframe: str, count: int = 500,
                     start=None, end=None) -> pd.DataFrame:
        self._ensure_init()
        mt5 = self._mt5
        ticker = self._ticker(symbol)
        tf_attr = _TIMEFRAME_ATTR.get(timeframe)
        if tf_attr is None:
            raise ProviderUnavailable(f"Unsupported timeframe for MT5: {timeframe}")
        tf = getattr(mt5, tf_attr)

        if not mt5.symbol_select(ticker, True):
            raise ProviderUnavailable(f"MT5 symbol_select failed for '{ticker}' "
                                       "(check broker's exact symbol name in config.yaml)")

        if start is not None:
            rates = mt5.copy_rates_range(ticker, tf, start, end or datetime.now(timezone.utc))
        else:
            rates = mt5.copy_rates_from_pos(ticker, tf, 0, count)

        if rates is None or len(rates) == 0:
            raise ProviderUnavailable(f"MT5 returned no candles for '{ticker}' ({timeframe})")

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time").rename(columns={"tick_volume": "volume"})
        return self.normalize(df)

    def get_live_price(self, symbol: str) -> PriceQuote:
        self._ensure_init()
        mt5 = self._mt5
        ticker = self._ticker(symbol)
        mt5.symbol_select(ticker, True)
        tick = mt5.symbol_info_tick(ticker)
        if tick is None:
            raise ProviderUnavailable(f"MT5 has no tick for '{ticker}'")
        mid = (tick.bid + tick.ask) / 2.0
        return PriceQuote(symbol=symbol, price=mid, is_live=True, provider=self.name)

    def shutdown(self):
        if self._initialized and self._mt5:
            self._mt5.shutdown()
            self._initialized = False
