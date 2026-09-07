"""
Placeholder strategy — proves the pipeline end-to-end. NOT a real strategy.

Rule: long when close crosses above the 20-period SMA. Stop = entry - 1xATR(14).
Target sized for RR=2. That's it. Real strategies arrive in Phase 2, each as
its own file in this folder — this file is never touched by them.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from core.signal import Signal
from strategies.base import Strategy


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class DummySMA(Strategy):
    name = "dummy_sma"
    description = "Placeholder only: long on close crossing above the 20-period SMA."
    default_timeframe = "M15"

    def generate_signal(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Optional[Signal]:
        if len(df) < 25:
            return None

        sma = df["close"].rolling(20).mean()
        atr = _atr(df, 14)

        last_close, prev_close = df["close"].iloc[-1], df["close"].iloc[-2]
        last_sma, prev_sma = sma.iloc[-1], sma.iloc[-2]
        last_atr = atr.iloc[-1]

        if pd.isna(last_sma) or pd.isna(prev_sma) or pd.isna(last_atr) or last_atr <= 0:
            return None

        crossed_up = prev_close <= prev_sma and last_close > last_sma
        if not crossed_up:
            return None

        entry = float(last_close)
        stop = entry - float(last_atr)
        target = entry + 2 * (entry - stop)  # RR = 2

        return Signal(
            symbol=symbol,
            side="long",
            entry=entry,
            stop=stop,
            target=target,
            strategy_name=self.name,
            timeframe=timeframe,
            confidence=0.5,
            reason=f"close {entry:.2f} crossed above 20-SMA {last_sma:.2f} (ATR14={last_atr:.2f})",
            timestamp=df.index[-1].to_pydatetime(),
        )
