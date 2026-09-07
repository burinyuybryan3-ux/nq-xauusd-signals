"""
NQ / XAUUSD Signal Dashboard — Flask entry point.

Run locally:  python app.py
Then open:    http://127.0.0.1:5000
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import yaml
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from core import db
from core.backtester import run_backtest
from core.scheduler import SignalScheduler
from data_providers.base import ProviderUnavailable
from strategies.registry import discover_strategies

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")


def load_config() -> dict:
    with open(os.path.join(os.path.dirname(__file__), "config.yaml"), "r") as f:
        return yaml.safe_load(f)


CONFIG = load_config()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

db.init_db()
scheduler = SignalScheduler(CONFIG)
# Started at import time, not inside main() — a production WSGI server
# (gunicorn) imports this module directly and never calls main(), so the
# scheduler has to start here to run under both `python app.py` and gunicorn.
# Keep the process to a single worker (see Procfile): each worker would run
# its own copy of this thread, polling and writing to the same SQLite file.
scheduler.start()


# --------------------------------------------------------------------------- pages

@app.route("/")
def index():
    return render_template(
        "index.html",
        symbols=list(CONFIG["symbols"].keys()),
        timeframes=["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
    )


# --------------------------------------------------------------------------- API: status

@app.route("/api/status")
def api_status():
    out = {}
    for entry in CONFIG["watch"]:
        symbol = entry["symbol"]
        status = scheduler.last_poll_status.get(symbol)
        if status is None:
            try:
                quote = scheduler.provider_manager.get_live_price(symbol)
                status = {
                    "provider": quote.provider,
                    "is_live": quote.is_live,
                    "last_close": quote.price,
                    "at": datetime.utcnow().isoformat(),
                }
            except ProviderUnavailable as e:
                status = {"error": str(e), "at": datetime.utcnow().isoformat()}
        out[symbol] = status
    return jsonify(out)


# --------------------------------------------------------------------------- API: signals

@app.route("/api/signals")
def api_signals():
    symbol = request.args.get("symbol")
    rows = db.list_signals(symbol=symbol, limit=200)
    return jsonify(rows)


# --------------------------------------------------------------------------- API: strategies

@app.route("/api/strategies")
def api_strategies():
    out = []
    for name, strat in scheduler.strategies.items():
        out.append({
            "name": name,
            "description": strat.description,
            "default_timeframe": strat.default_timeframe,
            "enabled": db.get_strategy_enabled(name, default=True),
        })
    return jsonify(out)


@app.route("/api/strategies/<name>/toggle", methods=["POST"])
def api_strategy_toggle(name):
    if name not in scheduler.strategies:
        return jsonify({"error": f"unknown strategy '{name}'"}), 404
    current = db.get_strategy_enabled(name, default=True)
    db.set_strategy_enabled(name, not current)
    return jsonify({"name": name, "enabled": not current})


# --------------------------------------------------------------------------- API: backtest

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    body = request.get_json(force=True)
    strategy_name = body.get("strategy")
    symbol = body.get("symbol")
    timeframe = body.get("timeframe", "M15")
    count = int(body.get("count", 1500))

    if strategy_name not in scheduler.strategies:
        return jsonify({"error": f"unknown strategy '{strategy_name}'"}), 400
    if symbol not in CONFIG["symbols"]:
        return jsonify({"error": f"unknown symbol '{symbol}'"}), 400

    strategy = scheduler.strategies[strategy_name]
    tick_size = CONFIG["symbols"][symbol].get("tick_size", 0.25)
    bt_cfg = CONFIG.get("backtest", {})

    try:
        df, provider_name, is_live = scheduler.provider_manager.get_candles(
            symbol, timeframe, count=count, prefer="historical"
        )
    except ProviderUnavailable as e:
        return jsonify({"error": str(e)}), 502

    result = run_backtest(
        strategy, df, symbol, timeframe,
        slippage_ticks=bt_cfg.get("slippage_ticks", 1),
        tick_size=tick_size,
        commission_per_trade=bt_cfg.get("commission_per_trade", 0),
        starting_equity_r=bt_cfg.get("starting_equity_r", 0),
    )

    db.save_backtest_run(
        strategy_name, symbol, timeframe,
        df.index[0].isoformat(), df.index[-1].isoformat(),
        result.metrics, result.equity_curve,
    )

    return jsonify({
        "provider": provider_name,
        "is_live": is_live,
        "bars_used": len(df),
        "metrics": result.metrics,
        "equity_curve": result.equity_curve,
        "trades": [t.__dict__ for t in result.trades],
    })


# --------------------------------------------------------------------------- startup

def main():
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    # use_reloader=False: avoids a second process double-starting the scheduler thread.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()
