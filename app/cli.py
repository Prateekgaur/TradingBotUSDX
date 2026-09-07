"""Command-line runner, for when you want a result without the browser.

    python -m app.cli backtest  --symbol NIFTY50 --timeframe 1d --strategy donchian_breakout
    python -m app.cli sweep     --symbol RELIANCE --timeframe 15m --strategy vwap_pullback
    python -m app.cli walk      --symbol NIFTY50 --timeframe 1d --strategy donchian_breakout --folds 5
    python -m app.cli scan      --timeframe 15m --strategy opening_range_breakout

`scan` runs one strategy across every symbol you have data for, which is the
fastest way to see whether an edge is real or a property of one stock.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.data import loader
from app.engine.backtest import EngineConfig, run_backtest
from app.engine.metrics import compute
from app.engine.risk import RiskConfig
from app.optimize.sweep import run_sweep
from app.optimize.walkforward import walk_forward
from app.settings import get_instrument
from app.strategies import registry


def _cfg(a) -> EngineConfig:
    return EngineConfig(
        starting_equity=a.equity,
        risk=RiskConfig(risk_per_trade_pct=a.risk, daily_loss_limit_pct=a.daily_loss,
                        max_drawdown_pct=a.max_dd, allow_short=not a.long_only))


def _params(a) -> dict:
    return json.loads(a.params) if a.params else {}


def _print_stats(title: str, s: dict) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    for k in ("trades", "return_pct", "cagr_pct", "max_drawdown_pct", "win_rate_pct",
              "profit_factor", "expectancy_r", "sharpe", "total_costs",
              "buy_hold_return_pct", "grade"):
        print(f"  {k:<22} {s.get(k)}")
    for w in s.get("warnings", []):
        print(f"  [{w['severity']:>6}] {w['message']}")


def cmd_backtest(a) -> int:
    ds = loader.load(a.symbol, a.timeframe, a.start, a.end)
    for w in ds.warnings:
        print(f"  data note: {w}")
    res = run_backtest(ds.df, registry.build(a.strategy, _params(a)),
                       get_instrument(a.symbol), _cfg(a))
    s = compute(res.trades, res.equity, res.price, res.starting_equity)
    _print_stats(f"{a.strategy} | {ds.symbol} {ds.timeframe} | {ds.start:%Y-%m-%d} to {ds.end:%Y-%m-%d}", s)
    if res.halted:
        print(f"  HALTED: {res.halt_reason}")
    if res.blocks:
        print(f"  entries refused by risk rules: {res.blocks}")
    return 0


def cmd_sweep(a) -> int:
    ds = loader.load(a.symbol, a.timeframe, a.start, a.end)
    rows = run_sweep(ds.df, a.strategy, get_instrument(a.symbol), _cfg(a),
                     only=a.only, overrides=_params(a) or None, objective=a.objective,
                     max_evals=a.max_evals, min_trades=a.min_trades)
    print(f"\ntop {min(a.top, len(rows))} of {len(rows)} combinations (IN SAMPLE -- shortlist only)")
    print(f"{'score':>8} {'ret%':>8} {'dd%':>7} {'pf':>6} {'exp R':>7} {'n':>5}  params")
    for r in rows[: a.top]:
        if r.score == float("-inf"):
            continue
        st = r.stats
        print(f"{r.score:8.3f} {st['return_pct']:8.2f} {st['max_drawdown_pct']:7.2f} "
              f"{str(st['profit_factor']):>6} {st['expectancy_r']:7.3f} {st['trades']:5}  {r.params}")
    print("\nThese numbers were fitted to this exact history. Walk-forward them before believing.")
    return 0


def cmd_walk(a) -> int:
    ds = loader.load(a.symbol, a.timeframe, a.start, a.end)
    wf = walk_forward(ds.df, a.strategy, get_instrument(a.symbol), _cfg(a),
                      folds=a.folds, train_frac=a.train_frac, objective=a.objective,
                      max_evals=a.max_evals, min_trades=a.min_trades, only=a.only,
                      overrides=_params(a) or None)
    print(f"\n{'fold':>4} {'test window':>25} {'in-sample':>10} {'out-of-sample':>14} {'trades':>7}  params")
    for f in wf.folds:
        print(f"{f.index:>4} {f.test_start[:10]}..{f.test_end[:10]:>10} "
              f"{f.train_stats.get('return_pct', 0):9.2f}% {f.test_stats.get('return_pct', 0):13.2f}% "
              f"{f.test_stats.get('trades', 0):7}  {f.best_params}")
    _print_stats(f"OUT-OF-SAMPLE (stitched) | verdict: {wf.verdict.upper()} | efficiency {wf.efficiency}",
                 wf.oos_stats)
    for n in wf.notes:
        print(f"  * {n}")
    return 0 if wf.verdict != "reject" else 1


def cmd_scan(a) -> int:
    syms = a.symbols or sorted({d["symbol"] for d in loader.available()
                                if d["timeframe"] == a.timeframe})
    print(f"\n{a.strategy} across {len(syms)} symbols @ {a.timeframe}")
    print(f"{'symbol':<12} {'trades':>7} {'ret%':>8} {'dd%':>7} {'pf':>6} {'exp R':>7}  grade")
    agg = []
    for sym in syms:
        try:
            ds = loader.load(sym, a.timeframe, a.start, a.end)
            res = run_backtest(ds.df, registry.build(a.strategy, _params(a)),
                               get_instrument(sym), _cfg(a))
            s = compute(res.trades, res.equity, res.price, res.starting_equity)
        except Exception as e:
            print(f"{sym:<12} {type(e).__name__}: {e}")
            continue
        agg.append(s["return_pct"])
        print(f"{sym:<12} {s['trades']:7} {s['return_pct']:8.2f} {s['max_drawdown_pct']:7.2f} "
              f"{str(s['profit_factor']):>6} {s['expectancy_r']:7.3f}  {s['grade']}")
    if agg:
        winners = sum(1 for x in agg if x > 0)
        print(f"\nprofitable on {winners}/{len(agg)} symbols, average return "
              f"{sum(agg) / len(agg):+.2f}%")
        print("An edge that only works on one symbol is usually not an edge.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.cli", description="Trading Lab command line")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, need_symbol=True):
        if need_symbol:
            p.add_argument("--symbol", required=True)
        p.add_argument("--timeframe", default="15m")
        p.add_argument("--strategy", required=True, choices=sorted(registry.BY_NAME))
        p.add_argument("--params", default="", help='JSON, e.g. \'{"fast":5,"slow":30}\'')
        p.add_argument("--start", default=None)
        p.add_argument("--end", default=None)
        p.add_argument("--equity", type=float, default=100_000)
        p.add_argument("--risk", type=float, default=1.0, help="%% of equity per trade")
        p.add_argument("--daily-loss", dest="daily_loss", type=float, default=3.0)
        p.add_argument("--max-dd", dest="max_dd", type=float, default=25.0)
        p.add_argument("--long-only", action="store_true")

    def searchy(p):
        p.add_argument("--objective", default="robust")
        p.add_argument("--max-evals", dest="max_evals", type=int, default=250)
        p.add_argument("--min-trades", dest="min_trades", type=int, default=20)
        p.add_argument("--only", nargs="*", default=None, help="parameters to search")

    p = sub.add_parser("backtest"); common(p); p.set_defaults(fn=cmd_backtest)
    p = sub.add_parser("sweep"); common(p); searchy(p)
    p.add_argument("--top", type=int, default=15); p.set_defaults(fn=cmd_sweep)
    p = sub.add_parser("walk"); common(p); searchy(p)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--train-frac", dest="train_frac", type=float, default=0.7)
    p.set_defaults(fn=cmd_walk)
    p = sub.add_parser("scan"); common(p, need_symbol=False)
    p.add_argument("--symbols", nargs="*", default=None); p.set_defaults(fn=cmd_scan)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
