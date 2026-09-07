"""
DataProviderManager — the only thing the rest of the app talks to.

Tries providers in the priority order given by config.yaml (separate chains
for "live" and "historical"), falls through on ProviderUnavailable, and
transparently caches every successful candle fetch in SQLite so repeated
calls (and rate-limited providers) don't re-hit the network every poll.
"""
from __future__ import annotations

import logging

import pandas as pd

from core import db
from data_providers.base import DataProvider, ProviderUnavailable, PriceQuote
from data_providers.mt5_provider import MT5Provider
from data_providers.oanda_provider import OANDAProvider
from data_providers.dukascopy_provider import DukascopyProvider
from data_providers.twelvedata_provider import TwelveDataProvider
from data_providers.yfinance_provider import YFinanceProvider

log = logging.getLogger("data_providers")

_PROVIDER_CLASSES = {
    "mt5": MT5Provider,
    "oanda": OANDAProvider,
    "dukascopy": DukascopyProvider,
    "twelvedata": TwelveDataProvider,
    "yfinance": YFinanceProvider,
}


class DataProviderManager:
    def __init__(self, config: dict):
        self.config = config
        symbol_map = config["symbols"]
        self.providers: dict[str, DataProvider] = {
            key: cls(symbol_map) for key, cls in _PROVIDER_CLASSES.items()
        }
        self.live_chain = config["data"]["live_provider_priority"]
        self.historical_chain = config["data"]["historical_provider_priority"]
        self.cache_max_age_minutes = config["data"].get("cache_max_age_minutes", 5)

    def get_live_price(self, symbol: str) -> PriceQuote:
        errors = []
        for name in self.live_chain:
            provider = self.providers[name]
            try:
                return provider.get_live_price(symbol)
            except ProviderUnavailable as e:
                errors.append(f"{name}: {e}")
                continue
        # Fall through to a delayed quote rather than nothing.
        for name in self.historical_chain:
            provider = self.providers[name]
            try:
                return provider.get_live_price(symbol)
            except ProviderUnavailable as e:
                errors.append(f"{name}: {e}")
                continue
        raise ProviderUnavailable(f"No provider could give a price for {symbol}: {errors}")

    def get_candles(self, symbol: str, timeframe: str, count: int = 500,
                     start=None, end=None, prefer: str = "live") -> tuple[pd.DataFrame, str, bool]:
        """Returns (df, provider_name, is_live). Tries live chain first
        (for the signal engine), falls back to historical chain (backtests,
        or when no live feed is configured)."""
        chain = self.live_chain if prefer == "live" else self.historical_chain
        fallback_chain = self.historical_chain if prefer == "live" else self.live_chain
        errors = []

        for name in list(chain) + [c for c in fallback_chain if c not in chain]:
            provider = self.providers[name]
            try:
                df = provider.get_candles(symbol, timeframe, count=count, start=start, end=end)
                db.cache_candles(name, symbol, timeframe, df)
                return df, name, provider.is_live
            except ProviderUnavailable as e:
                log.info("Provider %s unavailable for %s/%s: %s", name, symbol, timeframe, e)
                errors.append(f"{name}: {e}")
                continue

        cached = db.load_cached_candles(chain[0], symbol, timeframe, limit=count)
        if cached is not None and not cached.empty:
            log.warning("All providers failed for %s/%s, serving stale cache", symbol, timeframe)
            return cached, f"{chain[0]}-cache", False

        raise ProviderUnavailable(f"No provider or cache available for {symbol}/{timeframe}: {errors}")
