"""
Twelve Data provider — secondary historical source (free tier: 8 req/min,
800 req/day). Requires TWELVEDATA_API_KEY in .env.

Rate limiting: caller (DataProviderManager) is responsible for caching via
core.db.cache_candles; this class also does its own simple in-process
backoff on 429s so a single burst doesn't blow the daily quota.
"""
from __future__ import annotations

import os
import time

import pandas as pd
import requests

from data_providers.base import DataProvider, ProviderUnavailable, PriceQuote

_INTERVAL_MAP = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1day",
}

_BASE_URL = "https://api.twelvedata.com"


class TwelveDataProvider(DataProvider):
    name = "twelvedata"
    is_live = False  # free tier is end-of-bar, not tick-level — treat as delayed

    def __init__(self, symbol_map: dict):
        self.symbol_map = symbol_map
        self.api_key = os.getenv("TWELVEDATA_API_KEY")

    def _ticker(self, symbol: str) -> str:
        return self.map_symbol(symbol, self.symbol_map)

    def _get(self, path: str, params: dict, retries: int = 3):
        if not self.api_key:
            raise ProviderUnavailable("TWELVEDATA_API_KEY not set in .env")
        params = {**params, "apikey": self.api_key}
        backoff = 8
        for attempt in range(retries):
            resp = requests.get(f"{_BASE_URL}{path}", params=params, timeout=15)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code == 429 or body.get("code") == 429:
                time.sleep(backoff * (attempt + 1))
                continue
            if body.get("status") == "error":
                raise ProviderUnavailable(f"Twelve Data error: {body.get('message')}")
            return body
        raise ProviderUnavailable("Twelve Data rate-limited after retries")

    def get_candles(self, symbol: str, timeframe: str, count: int = 500,
                     start=None, end=None) -> pd.DataFrame:
        ticker = self._ticker(symbol)
        interval = _INTERVAL_MAP.get(timeframe)
        if not interval:
            raise ProviderUnavailable(f"Unsupported timeframe for Twelve Data: {timeframe}")

        params = {"symbol": ticker, "interval": interval, "outputsize": min(count, 5000)}
        if start:
            params["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end:
            params["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")

        body = self._get("/time_series", params)
        values = body.get("values")
        if not values:
            raise ProviderUnavailable(f"Twelve Data returned no candles for '{ticker}'")

        df = pd.DataFrame(values)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").rename(columns={
            "open": "open", "high": "high", "low": "low", "close": "close",
        })
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype(float)
        df["volume"] = df.get("volume", 0)
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        return self.normalize(df).tail(count)

    def get_live_price(self, symbol: str) -> PriceQuote:
        ticker = self._ticker(symbol)
        body = self._get("/price", {"symbol": ticker})
        if "price" not in body:
            raise ProviderUnavailable(f"Twelve Data has no price for '{ticker}'")
        return PriceQuote(
            symbol=symbol, price=float(body["price"]), is_live=False,
            provider=self.name, timeframe_note="delayed (Twelve Data free tier)",
        )
