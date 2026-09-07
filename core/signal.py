"""
Signal dataclass — the single shape every strategy, the validator, the
scheduler, the backtester, and the frontend agree on.

Nothing in this file should ever need to change when a new strategy is added.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Signal:
    symbol: str                 # logical symbol, e.g. "NQ" or "XAUUSD"
    side: str                   # 'long' | 'short'
    entry: float
    stop: float
    target: float
    strategy_name: str
    timeframe: str
    reason: str = ""
    confidence: float = 0.5
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Populated by validation.py, not by strategies.
    rr: Optional[float] = None
    status: str = "pending"     # 'valid' | 'stale' | 'noise-risk' | 'invalid'
    status_reason: str = ""
    id: Optional[int] = None

    def __post_init__(self):
        if self.side not in ("long", "short"):
            raise ValueError(f"Signal.side must be 'long' or 'short', got {self.side!r}")
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)

    def compute_rr(self) -> float:
        risk = abs(self.entry - self.stop)
        reward = abs(self.target - self.entry)
        if risk == 0:
            return 0.0
        return round(reward / risk, 3)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_row(cls, row: dict) -> "Signal":
        ts = row["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return cls(
            id=row.get("id"),
            symbol=row["symbol"],
            side=row["side"],
            entry=row["entry"],
            stop=row["stop"],
            target=row["target"],
            strategy_name=row["strategy_name"],
            timeframe=row["timeframe"],
            reason=row.get("reason", ""),
            confidence=row.get("confidence", 0.5),
            timestamp=ts,
            rr=row.get("rr"),
            status=row.get("status", "pending"),
            status_reason=row.get("status_reason", ""),
        )
