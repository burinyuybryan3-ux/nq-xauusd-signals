"""
Signal service: polls each configured symbol/timeframe on an interval, runs
every enabled strategy, validates, dedupes, and stores new signals.

Runs in a background thread started from app.py — no external scheduler
dependency needed for a single-process Flask app like this.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from core import db
from core.validation import ValidationConfig, validate_signal, signal_age_minutes
from core.signal import Signal
from data_providers import DataProviderManager
from data_providers.base import ProviderUnavailable
from strategies.registry import discover_strategies
from strategies.dummy_sma import _atr

log = logging.getLogger("scheduler")


def _dedup_key(signal: Signal) -> str:
    # Same strategy+symbol+timeframe+side+entry(rounded) shouldn't re-fire
    # every poll while the setup is still forming on the same bar.
    return f"{signal.strategy_name}|{signal.symbol}|{signal.timeframe}|{signal.side}|{round(signal.entry, 2)}"


def _is_session_open(symbol: str, now_utc: datetime, session_cfg: dict) -> bool:
    """Very lightweight session-gap guard: skip known closed windows
    (config-driven) rather than a full exchange calendar."""
    cfg = session_cfg.get(symbol)
    if not cfg:
        return True  # no session config -> assume always open (e.g. crypto-like)

    weekday = now_utc.weekday()  # Monday=0 ... Sunday=6
    if weekday in cfg.get("closed_weekdays", []):
        return False

    daily_close = cfg.get("daily_close_utc")
    if daily_close:
        close_h, close_m = map(int, daily_close.split(":"))
        window_min = cfg.get("daily_close_window_minutes", 30)
        close_minutes = close_h * 60 + close_m
        now_minutes = now_utc.hour * 60 + now_utc.minute
        if close_minutes <= now_minutes < close_minutes + window_min:
            return False

    return True


class SignalScheduler:
    def __init__(self, config: dict):
        self.config = config
        self.provider_manager = DataProviderManager(config)
        self.strategies = discover_strategies()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.validation_cfg_base = config.get("validation", {})
        self.session_cfg = config.get("sessions", {})
        self.last_poll_status: dict = {}

    def enabled_strategies(self):
        return {
            name: strat for name, strat in self.strategies.items()
            if db.get_strategy_enabled(name, default=True)
        }

    def poll_once(self):
        symbols_cfg = self.config["watch"]
        now = datetime.now(timezone.utc)

        for entry in symbols_cfg:
            symbol = entry["symbol"]
            timeframe = entry["timeframe"]

            if not _is_session_open(symbol, now, self.session_cfg):
                self.last_poll_status[symbol] = {"skipped": "session closed", "at": now.isoformat()}
                continue

            try:
                df, provider_name, is_live = self.provider_manager.get_candles(
                    symbol, timeframe, count=300, prefer="live"
                )
            except ProviderUnavailable as e:
                log.warning("No data for %s/%s: %s", symbol, timeframe, e)
                self.last_poll_status[symbol] = {"error": str(e), "at": now.isoformat()}
                continue

            self.last_poll_status[symbol] = {
                "provider": provider_name, "is_live": is_live, "at": now.isoformat(),
                "last_close": float(df["close"].iloc[-1]),
            }

            current_price = float(df["close"].iloc[-1])
            atr_series = _atr(df, 14)
            atr = float(atr_series.iloc[-1]) if not atr_series.empty and not atr_series.isna().all() else None

            tick_size = self.config["symbols"].get(symbol, {}).get("tick_size", 0.25)
            val_cfg = ValidationConfig(
                max_entry_distance_ticks=self.validation_cfg_base.get("max_entry_distance_ticks", 50),
                tick_size=tick_size,
                max_age_minutes=self.validation_cfg_base.get("max_age_minutes", 30),
                noise_risk_atr_mult=self.validation_cfg_base.get("noise_risk_atr_mult", 0.25),
            )

            existing_keys = db.recent_dedup_keys(minutes=self.validation_cfg_base.get("dedup_window_minutes", 240))

            for name, strategy in self.enabled_strategies().items():
                try:
                    signal = strategy.generate_signal(df, symbol, timeframe)
                except Exception:
                    log.exception("Strategy %s raised an exception on %s/%s", name, symbol, timeframe)
                    continue
                if signal is None:
                    continue

                key = _dedup_key(signal)
                if key in existing_keys:
                    continue

                validate_signal(signal, current_price, atr, val_cfg, now=now)
                db.insert_signal(signal, dedup_key=key)
                existing_keys.add(key)

                if signal.status in ("valid", "noise-risk"):
                    self._maybe_alert(signal)

    def _maybe_alert(self, signal: Signal):
        alert_cfg = self.config.get("alerts", {})
        if not alert_cfg.get("enabled", False):
            return
        msg = (f"[{signal.strategy_name}] {signal.symbol} {signal.side.upper()} "
               f"entry={signal.entry:.2f} stop={signal.stop:.2f} target={signal.target:.2f} "
               f"RR={signal.rr} status={signal.status}")
        log.info("ALERT: %s", msg)
        webhook_url = alert_cfg.get("webhook_url")
        if webhook_url:
            try:
                import requests
                requests.post(webhook_url, json={"content": msg}, timeout=10)
            except Exception:
                log.exception("Failed to send webhook alert")

    def _loop(self):
        interval = self.config["data"].get("poll_interval_seconds", 60)
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("Unhandled error in poll_once")
            self._stop_event.wait(interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="signal-scheduler")
        self._thread.start()
        log.info("Signal scheduler started (poll every %ss)", self.config["data"].get("poll_interval_seconds", 60))

    def stop(self):
        self._stop_event.set()
