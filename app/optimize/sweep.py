"""Parameter search.

Optimising a strategy on all of your data is how you build something that
would have been perfect yesterday and is worthless tomorrow. Two guards here:

  * the objective is not raw profit. Raw profit picks the single lucky corner
    of the parameter space. `robust` rewards a positive edge per unit of risk,
    scaled by how many trades produced it and penalised by drawdown.
  * results carry a `neighbourhood` score: how well the parameters AROUND the
    winner did. A peak standing alone on a cliff is a curve-fit; a peak on a
    broad plateau is more likely to be real.

Search alone still proves nothing out of sample -- see walkforward.py.
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

import pandas as pd

from app.engine.backtest import EngineConfig, run_backtest
from app.engine.metrics import compute
from app.settings import Instrument
from app.strategies import registry

OBJECTIVES = ("robust", "calmar", "sharpe", "profit_factor", "expectancy", "net_profit")


def score(stats: dict, objective: str = "robust", min_trades: int = 20) -> float:
    n = stats.get("trades", 0)
    if n < min_trades or stats.get("net_profit", 0) <= 0:
        return float("-inf")
    pf = stats.get("profit_factor") or 0.0
    dd = max(stats.get("max_drawdown_pct", 0.0), 0.5)
    if objective == "net_profit":
        return float(stats["net_profit"])
    if objective == "calmar":
        return float(stats.get("calmar", 0.0))
    if objective == "sharpe":
        return float(stats.get("sharpe", 0.0))
    if objective == "profit_factor":
        return float(pf)
    if objective == "expectancy":
        return float(stats.get("expectancy_r", 0.0))
    # robust: edge per unit risk, weighted by sample size, charged for drawdown
    return float(stats.get("expectancy_r", 0.0)) * math.sqrt(n) / (1.0 + dd / 10.0)


@dataclass
class SweepRow:
    params: dict
    stats: dict
    score: float


def candidates(strategy_name: str, only: list[str] | None = None,
               overrides: dict | None = None) -> list[dict]:
    cls = registry.get(strategy_name)
    only = list(only) if only else None
    # A parameter named in `only` is being searched, so the caller's current
    # value for it is a starting point, not a constraint. Everything else in
    # `overrides` is held fixed.
    fixed = {k: v for k, v in (overrides or {}).items() if not (only and k in only)}
    axes = {}
    for p in cls.params():
        if only is not None and p.name not in only:
            continue
        if p.name in fixed:
            continue
        g = p.grid()
        if len(g) > 1:
            axes[p.name] = g
    if not axes:
        return [dict(fixed)]
    keys = list(axes)
    return [dict(fixed, **dict(zip(keys, combo)))
            for combo in itertools.product(*(axes[k] for k in keys))]


def run_sweep(df: pd.DataFrame, strategy_name: str, inst: Instrument,
              cfg: EngineConfig, only: list[str] | None = None,
              overrides: dict | None = None, objective: str = "robust",
              max_evals: int = 400, min_trades: int = 20,
              seed: int = 0) -> list[SweepRow]:
    grid = candidates(strategy_name, only, overrides)
    if len(grid) > max_evals:
        random.Random(seed).shuffle(grid)      # random search beats a truncated grid
        grid = grid[:max_evals]

    rows: list[SweepRow] = []
    for params in grid:
        try:
            strat = registry.build(strategy_name, params)
            res = run_backtest(df, strat, inst, cfg)
            st = compute(res.trades, res.equity, res.price, res.starting_equity)
        except Exception as e:                 # a bad corner of the grid is not fatal
            rows.append(SweepRow(params, {"error": f"{type(e).__name__}: {e}", "trades": 0},
                                 float("-inf")))
            continue
        rows.append(SweepRow(params, st, score(st, objective, min_trades)))

    rows.sort(key=lambda r: r.score, reverse=True)
    _annotate_neighbourhood(rows)
    return rows


def _annotate_neighbourhood(rows: list[SweepRow], k: int = 8) -> None:
    """For each row, the mean score of the k most similar parameter sets. A
    winner whose neighbours all score badly is almost certainly noise."""
    finite = [r for r in rows if r.score > float("-inf")]
    if len(finite) < 3:
        for r in rows:
            r.stats["neighbourhood"] = None
        return
    keys = sorted({k for r in finite for k in r.params})
    spans = {}
    for key in keys:
        vals = [float(r.params[key]) for r in finite
                if isinstance(r.params.get(key), (int, float)) and not isinstance(r.params.get(key), bool)]
        spans[key] = (max(vals) - min(vals)) or 1.0 if vals else 1.0

    for r in rows:
        if r.score == float("-inf"):
            r.stats["neighbourhood"] = None
            continue
        dists = []
        for other in finite:
            if other is r:
                continue
            d = 0.0
            for key in keys:
                a, b = r.params.get(key), other.params.get(key)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
                        and not isinstance(a, bool):
                    d += abs(float(a) - float(b)) / spans[key]
                elif a != b:
                    d += 1.0
            dists.append((d, other.score))
        dists.sort(key=lambda x: x[0])
        near = [s for _, s in dists[:k]]
        r.stats["neighbourhood"] = round(sum(near) / len(near), 3) if near else None
