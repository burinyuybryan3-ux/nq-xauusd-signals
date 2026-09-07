"""
Backtester — the core of this app, not an afterthought. Runs any registered
strategy over historical candles with no lookahead bias: a strategy only
ever sees bars up to and including the last CLOSED bar, and any signal it
produces is filled on the NEXT bar's open (adjusted for slippage).

Metrics are reported primarily in R-multiples (multiples of the planned
risk per trade) so results are comparable across symbols/instruments
regardless of tick value or contract size. Raw price-based PnL is also
summed for reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from strategies.base import Strategy


@dataclass
class Trade:
    strategy_name: str
    symbol: str
    side: str
    signal_time: str
    entry_time: str
    entry: float
    stop: float
    target: float
    exit_time: str | None
    exit_price: float | None
    outcome: str          # 'win' | 'loss' | 'open'
    r_multiple: float
    pnl_price: float       # in instrument price units, before commission
    pnl_net: float          # pnl_price - commission, still in price units


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: list[dict]
    trades: list[Trade] = field(default_factory=list)


def run_backtest(
    strategy: Strategy,
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    slippage_ticks: float = 1.0,
    tick_size: float = 0.25,
    commission_per_trade: float = 0.0,
    starting_equity_r: float = 0.0,
    warmup_bars: int = 30,
) -> BacktestResult:
    """
    df must be ascending, UTC-indexed OHLCV covering the desired backtest
    window (plus warmup_bars extra at the front for indicators like SMA/ATR).
    """
    if len(df) < warmup_bars + 2:
        return BacktestResult(metrics=_empty_metrics(), equity_curve=[])

    slippage = slippage_ticks * tick_size
    trades: list[Trade] = []
    equity = starting_equity_r
    equity_curve = [{"timestamp": df.index[warmup_bars].isoformat(), "equity": equity}]

    open_position = None  # dict while a trade is live, else None
    i = warmup_bars

    while i < len(df) - 1:
        # Skip if already in a trade — one position at a time, resolved below.
        if open_position is None:
            visible = df.iloc[: i + 1]  # up to & including closed bar i — no lookahead
            signal = strategy.generate_signal(visible, symbol, timeframe)

            if signal is not None:
                fill_bar = i + 1
                raw_entry = float(df["open"].iloc[fill_bar])
                slip_adj = slippage if signal.side == "long" else -slippage
                fill_price = raw_entry + slip_adj

                open_position = {
                    "signal_time": df.index[i].isoformat(),
                    "entry_time": df.index[fill_bar].isoformat(),
                    "side": signal.side,
                    "entry": fill_price,
                    "stop": signal.stop,
                    "target": signal.target,
                    "risk": abs(signal.entry - signal.stop),
                    "scan_from": fill_bar,
                }

        if open_position is not None:
            scan_from = max(open_position["scan_from"], i + 1)
            for j in range(scan_from, len(df)):
                bar = df.iloc[j]
                hit_stop = (
                    bar["low"] <= open_position["stop"] if open_position["side"] == "long"
                    else bar["high"] >= open_position["stop"]
                )
                hit_target = (
                    bar["high"] >= open_position["target"] if open_position["side"] == "long"
                    else bar["low"] <= open_position["target"]
                )
                if hit_stop or hit_target:
                    # Conservative: if both touched in the same bar, assume stop hit first.
                    exit_price = open_position["stop"] if hit_stop else open_position["target"]
                    outcome = "loss" if hit_stop else "win"
                    pnl_price = (
                        (exit_price - open_position["entry"]) if open_position["side"] == "long"
                        else (open_position["entry"] - exit_price)
                    )
                    pnl_net = pnl_price - commission_per_trade
                    risk = open_position["risk"] or tick_size
                    r_multiple = pnl_net / risk

                    trades.append(Trade(
                        strategy_name=strategy.name, symbol=symbol, side=open_position["side"],
                        signal_time=open_position["signal_time"], entry_time=open_position["entry_time"],
                        entry=open_position["entry"], stop=open_position["stop"], target=open_position["target"],
                        exit_time=df.index[j].isoformat(), exit_price=exit_price,
                        outcome=outcome, r_multiple=r_multiple, pnl_price=pnl_price, pnl_net=pnl_net,
                    ))
                    equity += r_multiple
                    equity_curve.append({"timestamp": df.index[j].isoformat(), "equity": equity})
                    i = j
                    open_position = None
                    break
            else:
                # Position never resolved within available data — mark open, stop scanning.
                trades.append(Trade(
                    strategy_name=strategy.name, symbol=symbol, side=open_position["side"],
                    signal_time=open_position["signal_time"], entry_time=open_position["entry_time"],
                    entry=open_position["entry"], stop=open_position["stop"], target=open_position["target"],
                    exit_time=None, exit_price=None, outcome="open", r_multiple=0.0, pnl_price=0.0, pnl_net=0.0,
                ))
                open_position = None
                break

        i += 1

    metrics = _compute_metrics(trades, equity_curve)
    return BacktestResult(metrics=metrics, equity_curve=equity_curve, trades=trades)


def _empty_metrics() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "open": 0, "win_rate": 0.0,
        "avg_rr": 0.0, "profit_factor": None, "expectancy_r": 0.0,
        "max_drawdown_r": 0.0, "sharpe": None, "total_r": 0.0,
    }


def _compute_metrics(trades: list[Trade], equity_curve: list[dict]) -> dict:
    closed = [t for t in trades if t.outcome in ("win", "loss")]
    if not closed:
        return _empty_metrics()

    wins = [t for t in closed if t.outcome == "win"]
    losses = [t for t in closed if t.outcome == "loss"]
    open_trades = [t for t in trades if t.outcome == "open"]

    gross_profit = sum(t.r_multiple for t in wins)
    gross_loss = abs(sum(t.r_multiple for t in losses))
    total_r = sum(t.r_multiple for t in closed)

    win_rate = len(wins) / len(closed) if closed else 0.0
    avg_rr = sum(t.r_multiple for t in wins) / len(wins) if wins else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))
    expectancy_r = total_r / len(closed) if closed else 0.0

    equities = [pt["equity"] for pt in equity_curve]
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)

    returns = [t.r_multiple for t in closed]
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = var ** 0.5
        sharpe = (mean_r / std_r) * (len(returns) ** 0.5) if std_r > 0 else None
    else:
        sharpe = None

    return {
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "open": len(open_trades),
        "win_rate": round(win_rate, 4),
        "avg_rr": round(avg_rr, 3),
        "profit_factor": round(profit_factor, 3) if isinstance(profit_factor, float) and profit_factor != float("inf") else profit_factor,
        "expectancy_r": round(expectancy_r, 3),
        "max_drawdown_r": round(max_dd, 3),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "total_r": round(total_r, 3),
    }
