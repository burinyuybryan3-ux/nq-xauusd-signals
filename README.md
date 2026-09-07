# NQ / XAUUSD Signal Dashboard — Phase 1

A dashboard that pulls live/near-live market data for NQ (Nasdaq-100) and
XAUUSD (spot gold), runs one or more strategies over it, validates every
signal before it's shown, and includes a backtester so you can check any
strategy against history before trusting it live.

**This is Phase 1: architecture only.** The one strategy shipped
(`dummy_sma`) is a placeholder that proves the pipeline works end-to-end —
not something to trade. Real strategies come in Phase 2, each as a new file
dropped into `strategies/`, with zero changes to any core file.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then edit .env if you're using OANDA/Twelve Data
python app.py
```

Open http://127.0.0.1:5000

**Nothing above requires an API key.** With no `.env` values set, the live
provider chain (MT5, OANDA) will fail over automatically to the historical
chain (Dukascopy → Twelve Data → yfinance), and yfinance needs no key at
all. You'll see the feed-status badge read "delayed" rather than "live" —
that's correct, not a bug. It's how you get the pipeline running today
and plug in a real live feed later without touching any other code.

---

## Architecture

```
app.py                  Flask routes + startup
config.yaml              symbols, timeframes, provider priority, validation thresholds
.env                      API keys (never committed — see .env.example)

data_providers/
  base.py                 DataProvider ABC — the contract every provider implements
  mt5_provider.py          live, local only (MetaTrader5 package + running terminal)
  oanda_provider.py        live, cloud-deployable (OANDA v20 practice REST API)
  dukascopy_provider.py    historical, primary
  twelvedata_provider.py   historical, secondary (rate-limited, free tier)
  yfinance_provider.py     historical/live fallback, delayed, no API key
  __init__.py              DataProviderManager — tries providers in config order,
                            caches every successful fetch to SQLite

strategies/
  base.py                  Strategy ABC — generate_signal(df, symbol, timeframe) -> Signal|None
  registry.py               auto-discovers every Strategy subclass in this folder
  dummy_sma.py               PLACEHOLDER — long on close crossing above 20-SMA

core/
  signal.py                 Signal dataclass — the shape every layer agrees on
  validation.py              mandatory checks every signal passes through
  backtester.py              bar-by-bar simulation, no lookahead, R-multiple metrics
  scheduler.py                background polling loop: fetch -> strategies -> validate -> store
  db.py                       SQLite: signals, candle cache, strategy toggles, backtest history

templates/index.html, static/    dashboard UI (Tailwind CDN + Lightweight Charts, dark)
```

### Data flow

```
scheduler.poll_once()  (every poll_interval_seconds)
  -> DataProviderManager.get_candles(symbol, timeframe)   [tries live chain, then historical chain]
  -> for each enabled strategy: strategy.generate_signal(df, symbol, timeframe)
  -> validation.validate_signal(...)                      [rr, status, status_reason]
  -> dedup check (same strategy+symbol+timeframe+side+entry within dedup_window_minutes)
  -> db.insert_signal(...)
  -> optional alert (webhook / log) if status in (valid, noise-risk)
```

The dashboard's `/api/signals` just reads what's already in SQLite — the
frontend never talks to a data provider directly.

---

## Data providers — how selection works

`config.yaml` has two provider chains:

```yaml
data:
  live_provider_priority: ["mt5", "oanda"]
  historical_provider_priority: ["dukascopy", "twelvedata", "yfinance"]
```

`DataProviderManager.get_candles(..., prefer="live")` tries every provider in
`live_provider_priority` first, then falls through to
`historical_provider_priority` if all of those raise `ProviderUnavailable`
(no terminal running, no API key, rate-limited, network error, symbol not
found). Every successful fetch is cached in SQLite (`candles_cache` table);
if every provider fails, the last good cache is served as a last resort
(logged as stale, not silently swapped in).

Backtests call with `prefer="historical"`, so they hit Dukascopy /
Twelve Data / yfinance directly rather than waiting on MT5/OANDA to fail
first.

### Symbol mapping

The same instrument has a different ticker per vendor. That mapping lives in
exactly one place, `config.yaml`:

```yaml
symbols:
  NQ:
    mt5: "NAS100"        # <-- check your broker's actual Market Watch symbol
    oanda: "NAS100_USD"
    yfinance: "NQ=F"
    ...
```

If your MT5 broker calls the Nasdaq-100 CFD `US100` or `USTEC` instead of
`NAS100`, change it here — nothing else needs to change.

### Feed-status badge

`DataProvider.is_live` is `True` only for MT5 and OANDA. Every historical
provider is `False`. The badge in the UI reads this directly — delayed data
is never presented as live.

### Using MT5 (default local provider)

1. Install the MetaTrader 5 terminal, log into your broker/prop demo account.
2. `pip install MetaTrader5` (Windows only — commented out of
   `requirements.txt` by default since it can't install on non-Windows CI).
3. Leave the terminal running. `mt5_provider.py` calls `mt5.initialize()`
   against whatever terminal is already logged in — it doesn't manage
   credentials itself.
4. Set `environment: local` and keep `mt5` first in `live_provider_priority`
   in `config.yaml` (already the default).

### Using OANDA (cloud-deployable alternative)

1. Sign up for a free OANDA practice account, generate a personal access
   token, and note your account ID.
2. Put both in `.env`:
   ```
   OANDA_API_KEY=...
   OANDA_ACCOUNT_ID=...
   ```
3. On a server where MT5 can't run, set `live_provider_priority: ["oanda"]`
   in `config.yaml` (or just leave `mt5` first — it will fail over to
   `oanda` automatically since MT5 raises `ProviderUnavailable` when there's
   no terminal to attach to).

### Historical / backtesting data

`dukascopy_provider.py` uses Dukascopy's free (unofficial) chart JSON
endpoint — no key needed, but undocumented and can change without notice.
If it breaks, the failure is contained to that one file; the manager falls
through to Twelve Data, then yfinance, automatically. `twelvedata_provider.py`
needs `TWELVEDATA_API_KEY` in `.env` (free tier: 8 req/min, 800/day — the
provider backs off on 429s, and the SQLite cache means you rarely re-hit
the same range twice).

### Timezones & sessions

All candle timestamps are normalized to UTC inside `DataProvider.normalize()`
before anything else touches them. The dashboard's configured display
timezone (`display.timezone` in config.yaml) is `America/Toronto` —
converted only at the UI layer, never stored. `config.yaml`'s `sessions:`
block is a lightweight (not exchange-calendar-grade) guard that skips
polling/signal generation across known closed windows — XAUUSD's daily
rollover gap and weekend closure, NQ's weekly closure — extend it if you
need Sunday-evening-open-specific handling.

---

## How to add a data provider

1. Create `data_providers/your_provider.py` subclassing `DataProvider`
   (`data_providers/base.py`). Implement `get_candles()` and
   `get_live_price()`, raising `ProviderUnavailable` on any failure rather
   than letting an unrelated exception escape (the manager only catches
   that specific exception to fall through to the next provider).
2. Register it in `data_providers/__init__.py`'s `_PROVIDER_CLASSES` dict.
3. Add its ticker mapping under each symbol in `config.yaml` (`symbols.NQ.your_provider: "..."`).
4. Add its name to `live_provider_priority` or `historical_provider_priority`
   wherever it should sit in the fallback order.

Nothing in `core/` needs to change.

---

## How to add a strategy (Phase 2 — this is what you'll do next)

1. Create `strategies/your_strategy.py`:

   ```python
   from strategies.base import Strategy
   from core.signal import Signal

   class YourStrategy(Strategy):
       name = "your_strategy"                 # unique — used for dedup, toggles, backtest selection
       description = "One line describing the rule."
       default_timeframe = "M15"

       def generate_signal(self, df, symbol, timeframe):
           # df is OHLCV up to and including the last CLOSED bar — no lookahead.
           # Return a Signal(...) for a new setup, or None.
           ...
   ```

2. That's it — `strategies/registry.py` auto-discovers any `Strategy`
   subclass in this folder on startup. No edits to `app.py`, `scheduler.py`,
   `validation.py`, or `backtester.py`.
3. Enable/disable it from the dashboard's Strategies panel, or by adding its
   `name` to `strategies.enabled` in `config.yaml`.
4. **Before it ever runs live**, use the Backtest panel: pick the strategy,
   symbol, timeframe and bar count, run it, and look at win rate / profit
   factor / expectancy / max drawdown / the equity curve. Every signal it
   produces — live or backtested — passes through the same mandatory
   validation in `core/validation.py`, so a strategy can't ship a signal
   with the stop on the wrong side of price or pinned inside noise; those
   get flagged automatically.

---

## Signal validation (mandatory, not optional)

Every signal from every strategy passes through `core/validation.py`
before it's stored:

| Check | Outcome |
|---|---|
| Ordering: long needs `stop < entry < target`, short needs `target < entry < stop` | fails -> `invalid`, never stored as active |
| Current price has already passed the stop | -> `stale` |
| Entry is more than `max_entry_distance_ticks` from current price | -> `stale` |
| Signal is older than `max_age_minutes` | -> `stale` |
| Stop distance < `noise_risk_atr_mult` × ATR(14) | -> `noise-risk` (shown, but flagged) |
| Everything else | -> `valid` |

Thresholds are in `config.yaml` under `validation:`. RR is computed as
`abs(target - entry) / abs(entry - stop)` and shown on every signal, along
with its age.

---

## Backtester

`core/backtester.py` runs a strategy bar-by-bar over historical candles:

- The strategy only ever sees `df.iloc[:i+1]` (bars up to and including the
  last closed one) — no lookahead.
- A signal fills on the **next** bar's open, adjusted by `slippage_ticks`.
- The trade resolves on whichever of stop/target is touched first in
  subsequent bars; if both are touched within the same bar, the stop is
  assumed to hit first (conservative).
- Metrics are reported in **R-multiples** (multiples of the planned risk per
  trade) so results are comparable across instruments regardless of tick
  value: total trades, win rate, avg RR on winners, profit factor,
  expectancy, max drawdown, Sharpe, and an equity curve (in R) rendered as
  a chart.

Run it from the dashboard's Backtest panel, or directly:

```python
from core.backtester import run_backtest
from data_providers import DataProviderManager
from strategies.registry import discover_strategies
import yaml

config = yaml.safe_load(open("config.yaml"))
mgr = DataProviderManager(config)
df, provider, is_live = mgr.get_candles("XAUUSD", "M15", count=2000, prefer="historical")
strategy = discover_strategies()["dummy_sma"]
result = run_backtest(strategy, df, "XAUUSD", "M15")
print(result.metrics)
```

---

## Alerts (off by default)

Set `alerts.enabled: true` in `config.yaml` and (optionally) a
`webhook_url` (Discord-compatible) to get a POST for every `valid` or
`noise-risk` signal, in addition to the console log line. Nothing fires
until you turn this on.

---

## Deploying to a free tier (Render / Railway)

MT5 cannot run there (no terminal to attach to). Set:

```yaml
environment: cloud
data:
  live_provider_priority: ["oanda"]
```

and set `OANDA_API_KEY` / `OANDA_ACCOUNT_ID` as environment variables on the
host. Everything else is unchanged — SQLite is file-based, so make sure the
platform's disk is persistent (or point `core/db.py`'s `DB_PATH` at a
mounted volume) if you want signal history to survive redeploys.

---

## What's deliberately NOT here yet (Phase 2)

- Any real strategy logic — `dummy_sma` exists purely to prove the pipeline.
- Position sizing / account risk management.
- Order execution — this app generates and validates signals, it does not
  place trades.

Send the rules for each real strategy and they'll land as new files in
`strategies/`, validated with the backtester before being enabled — with no
changes to `core/`, `data_providers/`, or `app.py`.
