"""
SQLite access layer. One file, plain sqlite3 (no ORM) — the schema is small
enough that an ORM would add indirection without buying anything.

Tables:
  signals          — every signal ever produced, with validation status.
  candles_cache    — local cache of OHLCV candles per (provider, symbol, timeframe).
  strategy_state   — enabled/disabled toggle per strategy, persisted across restarts.
  backtest_runs    — stored results of past backtests (so the UI can list history).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    rr REAL,
    confidence REAL,
    timeframe TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL,
    status_reason TEXT,
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    dedup_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_status ON signals(symbol, status);
CREATE INDEX IF NOT EXISTS idx_signals_dedup ON signals(dedup_key);

CREATE TABLE IF NOT EXISTS candles_cache (
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (provider, symbol, timeframe, ts)
);

CREATE TABLE IF NOT EXISTS strategy_state (
    strategy_name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    metrics_json TEXT NOT NULL,
    equity_curve_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_conn() -> sqlite3.Connection:
    """One connection per thread (Flask dev server + scheduler thread)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def cursor():
    conn = get_conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------- signals --

def insert_signal(signal, dedup_key: str) -> int:
    with cursor() as cur:
        cur.execute(
            """INSERT INTO signals
               (symbol, side, entry, stop, target, rr, confidence, timeframe,
                strategy_name, reason, status, status_reason, timestamp, dedup_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                signal.symbol, signal.side, signal.entry, signal.stop, signal.target,
                signal.rr, signal.confidence, signal.timeframe, signal.strategy_name,
                signal.reason, signal.status, signal.status_reason,
                signal.timestamp.isoformat(), dedup_key,
            ),
        )
        return cur.lastrowid


def recent_dedup_keys(minutes: int = 240) -> set[str]:
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT dedup_key FROM signals WHERE created_at >= datetime('now', ?)",
            (f"-{minutes} minutes",),
        )
        return {row["dedup_key"] for row in cur.fetchall()}


def list_signals(symbol: str | None = None, limit: int = 200) -> list[dict]:
    with cursor() as cur:
        if symbol:
            cur.execute(
                "SELECT * FROM signals WHERE symbol=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit),
            )
        else:
            cur.execute("SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,))
        return [dict(row) for row in cur.fetchall()]


# ------------------------------------------------------------- candle cache --

def cache_candles(provider: str, symbol: str, timeframe: str, df) -> None:
    with cursor() as cur:
        rows = [
            (provider, symbol, timeframe, ts.isoformat(), o, h, l, c, v)
            for ts, o, h, l, c, v in zip(
                df.index, df["open"], df["high"], df["low"], df["close"], df["volume"]
            )
        ]
        cur.executemany(
            """INSERT OR REPLACE INTO candles_cache
               (provider, symbol, timeframe, ts, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )


def load_cached_candles(provider: str, symbol: str, timeframe: str, limit: int = 1000):
    import pandas as pd
    with cursor() as cur:
        cur.execute(
            """SELECT ts, open, high, low, close, volume FROM candles_cache
               WHERE provider=? AND symbol=? AND timeframe=?
               ORDER BY ts DESC LIMIT ?""",
            (provider, symbol, timeframe, limit),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


# ------------------------------------------------------------ strategy state --

def get_strategy_enabled(name: str, default: bool = True) -> bool:
    with cursor() as cur:
        cur.execute("SELECT enabled FROM strategy_state WHERE strategy_name=?", (name,))
        row = cur.fetchone()
        if row is None:
            return default
        return bool(row["enabled"])


def set_strategy_enabled(name: str, enabled: bool) -> None:
    with cursor() as cur:
        cur.execute(
            """INSERT INTO strategy_state (strategy_name, enabled) VALUES (?,?)
               ON CONFLICT(strategy_name) DO UPDATE SET enabled=excluded.enabled""",
            (name, int(enabled)),
        )


# ------------------------------------------------------------- backtest runs --

def save_backtest_run(strategy_name, symbol, timeframe, start_date, end_date, metrics: dict, equity_curve: list) -> int:
    with cursor() as cur:
        cur.execute(
            """INSERT INTO backtest_runs
               (strategy_name, symbol, timeframe, start_date, end_date, metrics_json, equity_curve_json)
               VALUES (?,?,?,?,?,?,?)""",
            (strategy_name, symbol, timeframe, start_date, end_date,
             json.dumps(metrics), json.dumps(equity_curve)),
        )
        return cur.lastrowid
