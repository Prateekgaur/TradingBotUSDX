"""The bar loop.

Sequence per bar, and the reason for each step:

  1. roll the risk day / update the drawdown kill switch (uses equity as of the
     previous close -- the only equity we legitimately know at this open)
  2. fill any order the strategy placed on the previous bar, at THIS open
  3. walk the bar for stop / target hits, including on a position opened in
     step 2 (a trade can absolutely lose on the bar it was entered)
  4. mark equity at the close
  5. trail the stop, using the close of a bar already tested for exits
  6. session square-off and time stops
  7. ask the strategy for a signal -- which cannot fill until the next open

Steps 2 and 7 being at opposite ends of the bar is the whole no-lookahead
guarantee. Nothing in this file may be reordered without breaking it.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.engine.broker import Order, PaperBroker, Trade
from app.engine.risk import RiskConfig, RiskManager
from app.settings import Instrument
from app.strategies.base import Context, Exit, Signal, Strategy


@dataclass
class EngineConfig:
    starting_equity: float = 100_000.0
    risk: RiskConfig = field(default_factory=RiskConfig)
    max_bars_in_trade: int = 0        # 0 disables the time stop
    square_off_intraday: bool = True
    pessimistic_intrabar: bool = True
    warmup_override: int = 0


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    strategy: str
    params: dict
    trades: list[Trade]
    equity: pd.Series
    price: pd.DataFrame
    signals: list[dict]
    blocks: dict[str, int]
    halted: bool
    halt_reason: str
    starting_equity: float
    bars: int
    elapsed_s: float
    stats: dict = field(default_factory=dict)


def _atr_key(ind: dict) -> str | None:
    for k in ("atr", "atr14", "atr_stop"):
        if k in ind:
            return k
    return None


def run_backtest(df: pd.DataFrame, strategy: Strategy, inst: Instrument,
                 cfg: EngineConfig) -> BacktestResult:
    t0 = _time.perf_counter()
    n = len(df)
    ind = strategy.prepare(df, inst)
    ctx = Context(df, ind, inst)

    broker = PaperBroker(inst, cfg.starting_equity,
                         allow_short=cfg.risk.allow_short and not strategy.long_only,
                         pessimistic_intrabar=cfg.pessimistic_intrabar)
    risk = RiskManager(cfg.risk, cfg.starting_equity)

    o_ = df["open"].to_numpy(); h_ = df["high"].to_numpy()
    l_ = df["low"].to_numpy(); c_ = df["close"].to_numpy()
    idx = df.index
    atr_name = _atr_key(ind)
    atr_arr = np.asarray(ind[atr_name], dtype="float64") if atr_name else None

    warmup = cfg.warmup_override or strategy.warmup()
    warmup = max(2, min(warmup, max(2, n // 3)))

    pending_signal: Signal | None = None
    pending_exit: str | None = None
    equity_vals = np.full(n, cfg.starting_equity, dtype="float64")
    signals_log: list[dict] = []
    last_equity = cfg.starting_equity
    sq = inst.square_off
    prev_day = None

    for i in range(n):
        ctx.i = i
        ts = idx[i]
        o, h, l, c = o_[i], h_[i], l_[i], c_[i]
        day = ts.date()
        ctx.is_new_day = day != prev_day
        ctx.bar_of_day = 0 if ctx.is_new_day else ctx.bar_of_day + 1
        prev_day = day

        # 1 -----------------------------------------------------------------
        risk.on_bar(ts, last_equity)
        if ctx.is_new_day and strategy.intraday:
            # An intraday signal is about today's tape; it must not fill at
            # tomorrow's open. Swing strategies legitimately hold that order.
            pending_signal = None

        # 2 -----------------------------------------------------------------
        if pending_exit and broker.position is not None:
            broker.close_position(o, ts, pending_exit)
            risk.on_trade_closed(broker.trades[-1].pnl, i)
            last_equity = broker.equity
        pending_exit = None

        if pending_signal is not None and broker.position is None:
            sig = pending_signal
            ok, why = risk.can_enter(i, last_equity, sig.side)
            if not ok:
                risk.note_block(why)
            else:
                entry_ref = o
                d = 1 if sig.side == "long" else -1
                a = float(atr_arr[i]) if atr_arr is not None else float("nan")
                stop = sig.stop_price
                if stop is None and sig.stop_atr_mult and a == a and a > 0:
                    stop = entry_ref - d * sig.stop_atr_mult * a
                if stop is not None:
                    qty = risk.size(last_equity, entry_ref, stop, inst.tick_size,
                                    inst.point_value, inst.qty_step, inst.min_qty)
                    if qty > 0:
                        target = sig.target_price
                        if target is None and sig.target_r:
                            target = entry_ref + d * sig.target_r * abs(entry_ref - stop)
                        pos = broker.open_position(
                            Order(sig.side, qty, sig.reason, inst.round_price(stop),
                                  inst.round_price(target) if target else None,
                                  sig.trail_atr_mult, sig.tag),
                            o, ts, i)
                        if pos:
                            signals_log.append({
                                "time": str(ts), "type": "entry", "side": sig.side,
                                "price": pos.entry_price, "qty": pos.qty,
                                "stop": pos.stop_loss, "target": pos.take_profit,
                                "reason": sig.reason})
                    else:
                        risk.note_block("size_zero")
                else:
                    risk.note_block("no_stop")
        pending_signal = None

        # 3 -----------------------------------------------------------------
        t: Trade | None = broker.process_bar(o, h, l, c, ts)
        if t is not None:
            risk.on_trade_closed(t.pnl, i)
            signals_log.append({"time": str(ts), "type": "exit", "side": t.side,
                                "price": t.exit_price, "qty": t.qty,
                                "pnl": t.pnl, "reason": t.exit_reason})

        # 4 -----------------------------------------------------------------
        last_equity = broker.mark_to_market(c)
        equity_vals[i] = last_equity

        # 5 -----------------------------------------------------------------
        if broker.position is not None and atr_arr is not None:
            broker.update_trailing(c, float(atr_arr[i]))

        # 6 -----------------------------------------------------------------
        if broker.position is not None:
            force = None
            if cfg.square_off_intraday and strategy.intraday:
                nxt_day = idx[i + 1].date() if i + 1 < n else None
                if ts.time() >= sq or nxt_day != day:
                    force = "square_off"
            if force is None and cfg.max_bars_in_trade and \
                    broker.position.bars_held >= cfg.max_bars_in_trade:
                force = "time_stop"
            if force is None and i == n - 1:
                force = "end_of_data"
            if force:
                tt = broker.close_position(c, ts, force)
                if tt:
                    risk.on_trade_closed(tt.pnl, i)
                    signals_log.append({"time": str(ts), "type": "exit", "side": tt.side,
                                        "price": tt.exit_price, "qty": tt.qty,
                                        "pnl": tt.pnl, "reason": force})
                last_equity = broker.equity
                equity_vals[i] = last_equity

        # 7 -----------------------------------------------------------------
        if i < warmup or i == n - 1 or risk.state.halted:
            continue
        ctx.position = broker.position
        ctx.equity = last_equity
        ctx.bars_since_entry = broker.position.bars_held if broker.position else 0
        ctx.minutes_to_close = _minutes_to(ts, sq)

        ctx.request_breakeven = False
        out = strategy.on_bar(ctx)
        if ctx.request_breakeven and broker.position is not None:
            broker.move_stop_to_breakeven()
        if isinstance(out, Exit):
            if broker.position is not None:
                pending_exit = out.reason
        elif isinstance(out, Signal):
            if broker.position is None:
                pending_signal = out
            elif out.side != broker.position.side:
                pending_exit = "reverse"     # flip: flat first, re-enter next bar

    equity = pd.Series(equity_vals, index=idx, name="equity")
    return BacktestResult(
        symbol=str(getattr(df, "attrs", {}).get("symbol", inst.symbol)),
        timeframe=str(getattr(df, "attrs", {}).get("timeframe", "")),
        strategy=strategy.name, params=dict(strategy.p), trades=broker.trades,
        equity=equity, price=df, signals=signals_log,
        blocks=dict(risk.state.blocks), halted=risk.state.halted,
        halt_reason=risk.state.halt_reason, starting_equity=cfg.starting_equity,
        bars=n, elapsed_s=_time.perf_counter() - t0)


def _minutes_to(ts: pd.Timestamp, close_time) -> float:
    now = ts.hour * 60 + ts.minute
    end = close_time.hour * 60 + close_time.minute
    return float(end - now)
