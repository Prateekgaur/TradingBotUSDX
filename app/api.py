"""Local HTTP API + static UI host.

Binds to 127.0.0.1 only. Nothing here places a real order or talks to a broker;
the only outbound network call in the whole project is the Yahoo seeder, and
that runs from the command line, not from a request handler.
"""
from __future__ import annotations

import traceback
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import store
from app.data import loader
from app.engine.backtest import EngineConfig, run_backtest
from app.engine.metrics import compute
from app.engine.risk import RiskConfig
from app.optimize.sweep import OBJECTIVES, run_sweep
from app.optimize.walkforward import walk_forward
from app.settings import INSTRUMENTS, get_instrument
from app.strategies import registry

app = FastAPI(title="Trading Lab", version="1.0", docs_url="/api/docs")
STATIC = __import__("pathlib").Path(__file__).parent / "static"


# --------------------------------------------------------------------- models
class RiskIn(BaseModel):
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 100.0
    daily_loss_limit_pct: float = 3.0
    daily_profit_lock_pct: float = 0.0
    max_drawdown_pct: float = 25.0
    max_trades_per_day: int = 0
    loss_streak_pause: int = 0
    pause_bars: int = 10
    allow_short: bool = True
    fixed_qty: float = 0.0


class RunIn(BaseModel):
    symbol: str
    timeframe: str = "15m"
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None
    starting_equity: float = 100_000.0
    risk: RiskIn = Field(default_factory=RiskIn)
    max_bars_in_trade: int = 0
    pessimistic_intrabar: bool = True
    save: bool = True
    label: str = ""


class SweepIn(RunIn):
    objective: Literal["robust", "calmar", "sharpe", "profit_factor",
                       "expectancy", "net_profit"] = "robust"
    only: list[str] | None = None
    max_evals: int = 250
    min_trades: int = 20
    top: int = 25


class WalkIn(SweepIn):
    folds: int = 5
    train_frac: float = 0.7


# ---------------------------------------------------------------------- utils
def _engine_cfg(r: RunIn) -> EngineConfig:
    return EngineConfig(
        starting_equity=r.starting_equity,
        risk=RiskConfig(**r.risk.model_dump()),
        max_bars_in_trade=r.max_bars_in_trade,
        pessimistic_intrabar=r.pessimistic_intrabar)


def _load(r: RunIn) -> loader.Dataset:
    try:
        return loader.load(r.symbol, r.timeframe, r.start, r.end)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


def _epoch(ts) -> int:
    """Bar timestamps are naive exchange-local. The chart renders in UTC, so
    label them as UTC to keep 09:15 on the screen at 09:15."""
    return int(pd.Timestamp(ts).tz_localize("UTC").timestamp())


def _candles(df: pd.DataFrame, cap: int = 20_000) -> list[dict]:
    d = df if len(df) <= cap else df.iloc[-cap:]
    return [{"time": _epoch(t), "open": float(o), "high": float(h),
             "low": float(l), "close": float(c)}
            for t, o, h, l, c in zip(d.index, d["open"], d["high"], d["low"], d["close"])]


def _line(s: pd.Series, cap: int = 20_000) -> list[dict]:
    d = s if len(s) <= cap else s.iloc[-cap:]
    return [{"time": _epoch(t), "value": round(float(v), 2)} for t, v in d.items()]


def _markers(trades) -> list[dict]:
    out = []
    for t in trades:
        long = t.side == "long"
        out.append({"time": _epoch(t.entry_time), "position": "belowBar" if long else "aboveBar",
                    "color": "#2f9e6e" if long else "#c2554d",
                    "shape": "arrowUp" if long else "arrowDown",
                    "text": f"{'BUY' if long else 'SELL'} {t.qty:g}"})
        out.append({"time": _epoch(t.exit_time), "position": "aboveBar" if long else "belowBar",
                    "color": "#6b7280" if t.pnl >= 0 else "#c2554d", "shape": "square",
                    "text": f"{t.exit_reason} {t.pnl:+.0f}"})
    out.sort(key=lambda m: m["time"])
    return out


# ----------------------------------------------------------------------- meta
@app.get("/api/datasets")
def datasets():
    return {"datasets": loader.available(),
            "known_symbols": sorted(INSTRUMENTS)}


@app.get("/api/strategies")
def strategies():
    return {"strategies": registry.describe_all(), "objectives": list(OBJECTIVES)}


@app.get("/api/instruments")
def instruments():
    return {"instruments": [
        {"symbol": i.symbol, "name": i.name, "currency": i.currency,
         "tick_size": i.tick_size, "commission_pct": i.commission_pct,
         "slippage_ticks": i.slippage_ticks, "session": f"{i.session_open}-{i.session_close}",
         "square_off": str(i.square_off), "tags": list(i.tags)}
        for i in INSTRUMENTS.values()]}


# ------------------------------------------------------------------- backtest
@app.post("/api/backtest")
def backtest(r: RunIn):
    ds = _load(r)
    inst = get_instrument(r.symbol)
    try:
        strat = registry.build(r.strategy, r.params)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))

    res = run_backtest(ds.df, strat, inst, _engine_cfg(r))
    stats = compute(res.trades, res.equity, res.price, res.starting_equity)

    payload = {
        "symbol": ds.symbol, "timeframe": ds.timeframe, "strategy": r.strategy,
        "params": res.params, "stats": stats,
        "candles": _candles(ds.df), "equity": _line(res.equity),
        "markers": _markers(res.trades),
        "trades": [t.to_dict() for t in res.trades],
        "blocks": res.blocks, "halted": res.halted, "halt_reason": res.halt_reason,
        "data_warnings": ds.warnings, "source": ds.source,
        "bars": res.bars, "elapsed_s": round(res.elapsed_s, 3),
        "range": [str(ds.start), str(ds.end)],
        "instrument": {"currency": inst.currency, "tick_size": inst.tick_size,
                       "slippage_ticks": inst.slippage_ticks,
                       "commission_pct": inst.commission_pct},
    }
    if r.save:
        payload["run_id"] = store.save(
            "backtest", ds.symbol, ds.timeframe, r.strategy, res.params,
            r.model_dump(), stats,
            {k: payload[k] for k in ("trades", "blocks", "data_warnings", "range")},
            r.label)
    return payload


@app.post("/api/sweep")
def sweep(r: SweepIn):
    ds = _load(r)
    inst = get_instrument(r.symbol)
    rows = run_sweep(ds.df, r.strategy, inst, _engine_cfg(r), only=r.only,
                     overrides=r.params or None, objective=r.objective,
                     max_evals=r.max_evals, min_trades=r.min_trades)
    top = [{"params": x.params,
            "score": None if x.score == float("-inf") else round(x.score, 4),
            "trades": x.stats.get("trades", 0),
            "return_pct": x.stats.get("return_pct"),
            "max_drawdown_pct": x.stats.get("max_drawdown_pct"),
            "profit_factor": x.stats.get("profit_factor"),
            "expectancy_r": x.stats.get("expectancy_r"),
            "sharpe": x.stats.get("sharpe"),
            "neighbourhood": x.stats.get("neighbourhood"),
            "grade": x.stats.get("grade")}
           for x in rows[: r.top]]
    tested = len(rows)
    viable = sum(1 for x in rows if x.score > float("-inf"))
    payload = {
        "symbol": ds.symbol, "timeframe": ds.timeframe, "strategy": r.strategy,
        "objective": r.objective, "tested": tested, "viable": viable, "top": top,
        "caution": ("These are in-sample results. The best row here is the row that fit "
                    "this specific history most closely, which is not the same as the row "
                    "that will trade best next month. Run walk-forward before believing it."),
    }
    if r.save:
        payload["run_id"] = store.save("sweep", ds.symbol, ds.timeframe, r.strategy,
                                       r.params, r.model_dump(),
                                       {"trades": tested, "verdict": f"{viable} viable"},
                                       {"top": top}, r.label)
    return payload


@app.post("/api/walkforward")
def walkforward(r: WalkIn):
    ds = _load(r)
    inst = get_instrument(r.symbol)
    try:
        wf = walk_forward(ds.df, r.strategy, inst, _engine_cfg(r), folds=r.folds,
                          train_frac=r.train_frac, objective=r.objective,
                          max_evals=r.max_evals, min_trades=r.min_trades, only=r.only,
                          overrides=r.params or None)
    except ValueError as e:
        raise HTTPException(400, str(e))

    payload = {
        "symbol": ds.symbol, "timeframe": ds.timeframe, "strategy": r.strategy,
        "verdict": wf.verdict, "efficiency": wf.efficiency, "notes": wf.notes,
        "oos_stats": wf.oos_stats, "in_sample_avg": wf.is_stats_avg,
        "param_stability": wf.param_stability,
        "oos_equity": _line(wf.oos_equity),
        "folds": [{"index": f.index, "train": [f.train_start, f.train_end],
                   "test": [f.test_start, f.test_end], "params": f.best_params,
                   "train_return_pct": f.train_stats.get("return_pct"),
                   "train_trades": f.train_stats.get("trades"),
                   "test_return_pct": f.test_stats.get("return_pct"),
                   "test_trades": f.test_stats.get("trades"),
                   "test_max_dd_pct": f.test_stats.get("max_drawdown_pct"),
                   "test_profit_factor": f.test_stats.get("profit_factor")}
                  for f in wf.folds],
    }
    if r.save:
        payload["run_id"] = store.save(
            "walkforward", ds.symbol, ds.timeframe, r.strategy, r.params,
            r.model_dump(), {**wf.oos_stats, "verdict": wf.verdict},
            {"folds": payload["folds"], "notes": wf.notes}, r.label)
    return payload


# ----------------------------------------------------------------------- runs
@app.get("/api/runs")
def runs(limit: int = 60):
    return {"runs": store.list_runs(limit)}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    r = store.get(run_id)
    if not r:
        raise HTTPException(404, "no such run")
    return r


@app.delete("/api/runs/{run_id}")
def run_delete(run_id: str):
    if not store.delete(run_id):
        raise HTTPException(404, "no such run")
    return {"deleted": run_id}


@app.exception_handler(Exception)
def unhandled(_request, exc: Exception):
    return JSONResponse(status_code=500, content={
        "detail": f"{type(exc).__name__}: {exc}",
        "trace": traceback.format_exc().splitlines()[-6:]})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
