"""
Mandatory signal validation.

Every signal produced by any strategy passes through validate_signal() before
it is stored or shown. This module is core — strategies never implement their
own validation, and this file should not need to change when a new strategy
is added.

Statuses:
  "valid"        — passes every check, safe to show as active.
  "stale"        — entry too far from current price, or too old, or the stop
                    has already been breached by current price. Not shown as
                    an active/tradeable signal.
  "noise-risk"   — structurally valid but the stop is implausibly tight
                    relative to recent volatility (likely to get wicked out).
                    Shown, but flagged.
  "invalid"      — fails basic ordering (stop/entry/target on the wrong side
                    for the direction). Never stored as an active signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from core.signal import Signal


@dataclass
class ValidationConfig:
    max_entry_distance_ticks: float = 50.0   # ticks of "point" distance allowed between entry and current price
    tick_size: float = 0.25                  # instrument tick size, from config.yaml per-symbol
    max_age_minutes: float = 30.0
    noise_risk_atr_mult: float = 0.25        # stop distance below this * ATR => noise-risk


def validate_signal(
    signal: Signal,
    current_price: float,
    atr: float | None,
    config: ValidationConfig,
    now: datetime | None = None,
) -> Signal:
    """Mutates and returns `signal` with .rr, .status, .status_reason set."""
    now = now or datetime.now(timezone.utc)

    signal.rr = signal.compute_rr()

    # 1. Ordering check — hard reject, never shown as active.
    if signal.side == "long":
        ordered = signal.stop < signal.entry < signal.target
    else:
        ordered = signal.target < signal.entry < signal.stop

    if not ordered:
        signal.status = "invalid"
        signal.status_reason = (
            f"Bad ordering for {signal.side}: stop={signal.stop}, entry={signal.entry}, "
            f"target={signal.target} (expected "
            f"{'stop<entry<target' if signal.side == 'long' else 'target<entry<stop'})"
        )
        return signal

    # 2. Stop already breached by current price.
    breached = (
        (signal.side == "long" and current_price <= signal.stop)
        or (signal.side == "short" and current_price >= signal.stop)
    )
    if breached:
        signal.status = "stale"
        signal.status_reason = f"Stop already breached: price={current_price}, stop={signal.stop}"
        return signal

    # 3. Entry too far from current price.
    tick_distance = abs(signal.entry - current_price) / config.tick_size if config.tick_size else 0
    if tick_distance > config.max_entry_distance_ticks:
        signal.status = "stale"
        signal.status_reason = (
            f"Entry {tick_distance:.1f} ticks from current price "
            f"(max {config.max_entry_distance_ticks})"
        )
        return signal

    # 4. Signal too old.
    age_minutes = (now - signal.timestamp).total_seconds() / 60.0
    if age_minutes > config.max_age_minutes:
        signal.status = "stale"
        signal.status_reason = f"Signal age {age_minutes:.1f}m exceeds max {config.max_age_minutes}m"
        return signal

    # 5. Noise-risk: stop too tight relative to volatility.
    stop_distance = abs(signal.entry - signal.stop)
    if atr and atr > 0 and stop_distance < config.noise_risk_atr_mult * atr:
        signal.status = "noise-risk"
        signal.status_reason = (
            f"Stop distance {stop_distance:.4f} < {config.noise_risk_atr_mult} x ATR ({atr:.4f}) "
            "— likely to get wicked out"
        )
        return signal

    signal.status = "valid"
    signal.status_reason = "OK"
    return signal


def signal_age_minutes(signal: Signal, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (now - signal.timestamp).total_seconds() / 60.0
