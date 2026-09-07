"""
OANDA v20 REST provider — the cloud-deployable LIVE alternative to MT5.

Free practice ("paper") account. Requires OANDA_API_KEY and OANDA_ACCOUNT_ID
in .env. Symbols: NAS100_USD, XAU_USD (mapped via config.yaml).

Docs: https://developer.oanda.com/rest-live-v20/introduction/
"""
from __future__ import annotations

import os
import time

import pandas as pd
import requests

from data_providers.base import DataProvider, ProviderUnavailable, PriceQuote

_GRANULARITY_MAP = {
    "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30",
    "H1": "H1", "H4": "H4", "D1": "D",
}

_BASE_URL = "https://api-fxpractice.oanda.com"


class OANDAProvider(DataProvider):
    name = "oanda"
    is_live = True

    def __init__(self, symbol_map: dict):
        self.symbol_map = symbol_map
        self.api_key = os.getenv("OANDA_API_KEY")
        self.account_id = os.getenv("OANDA_ACCOUNT_ID")
        self._session = requests.Session()

    def _headers(self):
        if not self.api_key:
            raise ProviderUnavailable("OANDA_API_KEY not set in .env")
        return {"Authorization": f"Bearer {self.api_key}"}

    def _ticker(self, symbol: str) -> str:
        return self.map_symbol(symbol, self.symbol_map)

    def _get(self, path: str, params: dict, retries: int = 3):
        url = f"{_BASE_URL}{path}"
        backoff = 1.5
        for attempt in range(retries):
            resp = self._session.get(url, headers=self._headers(), params=params, timeout=15)
            if resp.status_code == 429:
                time.sleep(backoff ** attempt)
                continue
            if resp.status_code != 200:
                raise ProviderUnavailable(f"OANDA {path} -> {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise ProviderUnavailable(f"OANDA {path} rate-limited after {retries} retries")

    def get_candles(self, symbol: str, timeframe: str, count: int = 500,
                     start=None, end=None) -> pd.DataFrame:
        ticker = self._ticker(symbol)
        granularity = _GRANULARITY_MAP.get(timeframe)
        if not granularity:
            raise ProviderUnavailable(f"Unsupported timeframe for OANDA: {timeframe}")

        params = {"granularity": granularity, "price": "M"}
        if start:
            params["from"] = start.isoformat()
            if end:
                params["to"] = end.isoformat()
        else:
            params["count"] = min(count, 5000)

        data = self._get(f"/v3/instruments/{ticker}/candles", params)
        candles = data.get("candles", [])
        if not candles:
            raise ProviderUnavailable(f"OANDA returned no candles for '{ticker}'")

        rows = []
        for c in candles:
            if not c.get("complete", True):
                continue
            mid = c["mid"]
            rows.append({
                "time": c["time"], "open": float(mid["o"]), "high": float(mid["h"]),
                "low": float(mid["l"]), "close": float(mid["c"]), "volume": c.get("volume", 0),
            })
        df = pd.DataFrame(rows).set_index("time")
        df.index = pd.to_datetime(df.index, utc=True)
        return self.normalize(df).tail(count)

    def get_live_price(self, symbol: str) -> PriceQuote:
        ticker = self._ticker(symbol)
        if not self.account_id:
            raise ProviderUnavailable("OANDA_ACCOUNT_ID not set in .env")
        data = self._get(
            f"/v3/accounts/{self.account_id}/pricing", {"instruments": ticker}
        )
        prices = data.get("prices", [])
        if not prices:
            raise ProviderUnavailable(f"OANDA has no price for '{ticker}'")
        p = prices[0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        return PriceQuote(symbol=symbol, price=(bid + ask) / 2, is_live=True, provider=self.name)
