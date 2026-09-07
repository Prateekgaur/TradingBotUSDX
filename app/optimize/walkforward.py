"""Walk-forward analysis -- the only result in this app worth acting on.

The procedure:

    |--- train 1 ---|- test 1 -|
              |--- train 2 ---|- test 2 -|
                        |--- train 3 ---|- test 3 -|

Optimise on each training window, then trade those parameters -- unchanged --
through the following window, which the optimiser never saw. Stitch the test
windows together and you have an equity curve produced entirely out of sample.

That curve is the closest thing a backtest can offer to an honest forecast. If
it is flat or negative while the in-sample curve soars, the strategy does not
have an edge; it has a memory of the training data. Better to learn that here
than with money.

`efficiency` is out-of-sample return divided by in-sample return. Above ~0.5 is
respectable. Near zero or negative means the parameters do not survive contact
with new data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.engine.backtest import EngineConfig, run_backtest
from app.engine.metrics import compute
from app.settings import Instrument
from app.strategies import registry
from app.optimize.sweep import run_sweep


@dataclass
class Fold:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: dict
    train_stats: dict
    test_stats: dict
    trades: list = field(default_factory=list)


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos_equity: pd.Series
    oos_stats: dict
    is_stats_avg: dict
    efficiency: float
    param_stability: dict
    verdict: str
    notes: list[str]


def walk_forward(df: pd.DataFrame, strategy_name: str, inst: Instrument,
                 cfg: EngineConfig, folds: int = 5, train_frac: float = 0.7,
                 objective: str = "robust", max_evals: int = 200,
                 min_trades: int = 10, only: list[str] | None = None,
                 overrides: dict | None = None) -> WalkForwardResult:
    n = len(df)
    if folds < 2 or n < 400:
        raise ValueError("walk-forward needs at least 2 folds and ~400 bars")

    # Reserve the first `train_frac` of the history for the first training
    # window, then split what remains into equal test windows. Every fold gets
    # a full-length training window, and no test bar is ever trained on.
    seg = int(n * (1.0 - train_frac) / folds)
    if seg < 40:
        raise ValueError(f"each out-of-sample window would be only {seg} bars; "
                         f"use fewer folds, a smaller train fraction, or more history")
    train_len = n - seg * folds

    results: list[Fold] = []
    oos_pieces: list[pd.Series] = []
    equity = cfg.starting_equity
    notes: list[str] = []

    for k in range(folds):
        test_lo = train_len + k * seg
        test_hi = min(test_lo + seg, n)
        train_lo = max(0, test_lo - train_len)
        if test_lo - train_lo < 200:
            notes.append(f"fold {k + 1}: skipped, only {test_lo - train_lo} training bars")
            continue

        train = df.iloc[train_lo:test_lo]
        test = df.iloc[test_lo:test_hi]

        train_cfg = _with_equity(cfg, cfg.starting_equity)
        rows = run_sweep(train, strategy_name, inst, train_cfg, only=only,
                         overrides=overrides, objective=objective,
                         max_evals=max_evals, min_trades=min_trades)
        best = rows[0] if rows and rows[0].score > float("-inf") else None
        if best is None:
            notes.append(f"fold {k + 1}: no parameter set cleared the minimum trade count "
                         f"in training; used defaults")
            params = {}
            train_stats = rows[0].stats if rows else {}
        else:
            params, train_stats = best.params, best.stats

        # Indicators need history. Feed the test run the bars immediately
        # before the window as well, but forbid trading during them, so a
        # 200-bar EMA is warm on the window's first tradable bar. Without this
        # every fold would start blind and take no trades.
        strat = registry.build(strategy_name, params)
        pad = min(max(strat.warmup(), 50), test_lo - train_lo)
        fed = df.iloc[test_lo - pad: test_hi]
        test_cfg = _with_equity(cfg, equity)
        test_cfg.warmup_override = pad
        res = run_backtest(fed, strat, inst, test_cfg)
        oos_equity_piece = res.equity.iloc[pad:]
        test_stats = compute(res.trades, oos_equity_piece, test, equity)
        equity = float(oos_equity_piece.iloc[-1])
        oos_pieces.append(oos_equity_piece)

        results.append(Fold(
            index=k + 1,
            train_start=str(train.index[0]), train_end=str(train.index[-1]),
            test_start=str(test.index[0]), test_end=str(test.index[-1]),
            best_params=params, train_stats=train_stats, test_stats=test_stats,
            trades=[t.to_dict() for t in res.trades]))

    if not results:
        raise ValueError("no usable folds -- give the run more history")

    oos_equity = pd.concat(oos_pieces)
    oos_equity = oos_equity[~oos_equity.index.duplicated(keep="last")].sort_index()
    price = df.loc[oos_equity.index[0]: oos_equity.index[-1]]
    all_trades = [t for f in results for t in f.trades]
    oos_stats = _stats_from_equity(oos_equity, price, cfg.starting_equity, all_trades)

    is_ret = float(np.mean([f.train_stats.get("return_pct", 0.0) for f in results]))
    oos_ret = float(np.mean([f.test_stats.get("return_pct", 0.0) for f in results]))
    efficiency = (oos_ret / is_ret) if is_ret > 0 else (0.0 if oos_ret <= 0 else 1.0)

    stability = _param_stability(results)
    verdict, more = _verdict(oos_stats, efficiency, stability, results)
    notes.extend(more)

    return WalkForwardResult(results, oos_equity, oos_stats,
                             {"return_pct": round(is_ret, 2)}, round(efficiency, 2),
                             stability, verdict, notes)


def _with_equity(cfg: EngineConfig, equity: float) -> EngineConfig:
    return EngineConfig(starting_equity=equity, risk=cfg.risk,
                        max_bars_in_trade=cfg.max_bars_in_trade,
                        square_off_intraday=cfg.square_off_intraday,
                        pessimistic_intrabar=cfg.pessimistic_intrabar,
                        warmup_override=cfg.warmup_override)


def _stats_from_equity(equity: pd.Series, price: pd.DataFrame,
                       starting_equity: float, trade_dicts: list[dict]) -> dict:
    from app.engine.broker import Trade

    trades = [Trade(side=t["side"], qty=t["qty"],
                    entry_time=pd.Timestamp(t["entry_time"]), entry_price=t["entry_price"],
                    exit_time=pd.Timestamp(t["exit_time"]), exit_price=t["exit_price"],
                    gross_pnl=t["gross_pnl"], costs=t["costs"], pnl=t["pnl"],
                    r_multiple=t["r_multiple"], bars_held=t["bars_held"],
                    exit_reason=t["exit_reason"], mae=t.get("mae", 0.0),
                    mfe=t.get("mfe", 0.0)) for t in trade_dicts]
    return compute(trades, equity, price, starting_equity)


def _param_stability(folds: list[Fold]) -> dict:
    """How much the winning parameters moved between folds. Parameters that
    jump around every fold are being fitted to noise."""
    keys = sorted({k for f in folds for k in f.best_params})
    out = {}
    for k in keys:
        vals = [f.best_params.get(k) for f in folds if k in f.best_params]
        nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(nums) >= 2 and np.mean(nums) != 0:
            cv = float(np.std(nums) / abs(np.mean(nums)))
            out[k] = {"values": vals, "cv": round(cv, 2),
                      "stable": bool(cv < 0.35)}
        else:
            out[k] = {"values": vals, "cv": None,
                      "stable": len(set(map(str, vals))) == 1}
    return out


def _verdict(oos: dict, efficiency: float, stability: dict,
             folds: list[Fold]) -> tuple[str, list[str]]:
    notes: list[str] = []
    profitable = [f for f in folds if f.test_stats.get("net_profit", 0) > 0]
    share = len(profitable) / len(folds)
    notes.append(f"{len(profitable)}/{len(folds)} out-of-sample windows were profitable.")

    unstable = [k for k, v in stability.items() if v.get("stable") is False]
    if unstable:
        notes.append("Parameters that changed materially between folds: "
                     + ", ".join(unstable) + ". Each refit is chasing recent noise.")

    if oos.get("net_profit", 0) <= 0:
        notes.append("Out of sample this strategy lost money. In-sample profit here is "
                     "curve fit, not edge. Do not trade it.")
        return "reject", notes
    if efficiency < 0.3:
        notes.append(f"Walk-forward efficiency {efficiency:.2f}: out-of-sample returns are a "
                     f"small fraction of in-sample. The parameters do not generalise.")
        return "reject", notes
    if share < 0.6 or oos.get("trades", 0) < 30:
        notes.append("Positive overall, but on thin or inconsistent evidence. "
                     "Extend the history before risking capital.")
        return "inconclusive", notes
    if oos.get("max_drawdown_pct", 100) > 25:
        notes.append(f"Out-of-sample drawdown {oos['max_drawdown_pct']}% is more than most "
                     f"people can hold through. Halve the risk per trade and re-run.")
        return "inconclusive", notes
    notes.append("Held up out of sample across folds. Next step is forward paper trading "
                 "on data that did not exist when these parameters were chosen.")
    return "survives", notes
