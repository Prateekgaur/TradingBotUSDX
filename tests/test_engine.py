"""Engine tests.

The first three are hand-computed: if the arithmetic in `broker.py` drifts,
these fail with a number you can check on paper. The rest are properties that
must hold for every strategy -- most importantly, that no strategy can profit
from a bar it has not seen yet.
"""
from __future__ import annotations

import sys
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.backtest import EngineConfig, run_backtest
from app.engine.broker import Order, PaperBroker
from app.engine.metrics import compute
from app.engine.risk import RiskConfig, RiskManager
from app.settings import Instrument
from app.strategies.base import Context, Exit, Param, Signal, Strategy

INST = Instrument("TEST", "Test", tick_size=0.01, qty_step=1.0, min_qty=1.0,
                  commission_pct=0.0, slippage_ticks=0.0, spread_ticks=0.0,
                  session_open=time(9, 15), session_close=time(15, 30),
                  square_off=time(15, 15))


def bars(rows, start="2024-01-02 09:15", freq="15min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    return df


# --------------------------------------------------------------------- broker
def test_long_win_is_exact():
    b = PaperBroker(INST, 100_000)
    ts = pd.Timestamp("2024-01-02 09:30")
    b.open_position(Order("long", 10, stop_loss=95.0, take_profit=110.0), 100.0, ts, 0)
    assert b.position.entry_price == 100.0
    t = b.process_bar(101, 111, 100.5, 110.5, ts)     # target inside the bar
    assert t is not None and t.exit_reason == "target"
    assert t.exit_price == 110.0
    assert t.pnl == pytest.approx(100.0)              # 10 * 10.0, no costs
    assert t.r_multiple == pytest.approx(2.0)         # risk was 5.0/unit


def test_stop_beats_target_when_both_hit():
    b = PaperBroker(INST, 100_000)
    ts = pd.Timestamp("2024-01-02 09:30")
    b.open_position(Order("long", 10, stop_loss=95.0, take_profit=110.0), 100.0, ts, 0)
    t = b.process_bar(100, 112, 94, 105, ts)          # both levels traded
    assert t.exit_reason == "stop_ambiguous"
    assert t.pnl == pytest.approx(-50.0)


def test_gap_through_stop_fills_at_the_open_not_the_stop():
    b = PaperBroker(INST, 100_000)
    ts = pd.Timestamp("2024-01-02 09:30")
    b.open_position(Order("long", 10, stop_loss=95.0), 100.0, ts, 0)
    t = b.process_bar(90, 91, 89, 90.5, ts)           # opened below the stop
    assert t.exit_reason == "stop_gap"
    assert t.exit_price == 90.0                       # NOT 95.0
    assert t.pnl == pytest.approx(-100.0)


def test_slippage_and_commission_always_hurt():
    inst = Instrument("S", "S", tick_size=0.01, slippage_ticks=5, commission_pct=0.001)
    b = PaperBroker(inst, 100_000)
    ts = pd.Timestamp("2024-01-02 09:30")
    b.open_position(Order("long", 10), 100.0, ts, 0)
    assert b.position.entry_price == pytest.approx(100.05)   # bought higher
    t = b.close_position(100.0, ts, "manual")
    assert t.exit_price == pytest.approx(99.95)              # sold lower
    assert t.pnl < 0 and t.costs > 0


def test_short_pnl_direction():
    b = PaperBroker(INST, 100_000)
    ts = pd.Timestamp("2024-01-02 09:30")
    b.open_position(Order("short", 10, stop_loss=105.0, take_profit=90.0), 100.0, ts, 0)
    t = b.process_bar(99, 99.5, 89, 90, ts)
    assert t.exit_reason == "target" and t.pnl == pytest.approx(100.0)


# ----------------------------------------------------------------------- risk
def test_position_size_risks_exactly_the_configured_percent():
    rm = RiskManager(RiskConfig(risk_per_trade_pct=1.0, max_position_pct=100_000), 100_000)
    qty = rm.size(100_000, entry=100.0, stop=95.0, tick_size=0.01,
                  point_value=1.0, qty_step=1.0, min_qty=1.0)
    assert qty == 200                       # 1000 risk / 5.0 per unit
    wide = rm.size(100_000, entry=100.0, stop=90.0, tick_size=0.01,
                   point_value=1.0, qty_step=1.0, min_qty=1.0)
    assert wide == 100                      # twice the stop -> half the size


def test_daily_loss_limit_blocks_further_entries():
    rm = RiskManager(RiskConfig(daily_loss_limit_pct=2.0), 100_000)
    rm.on_bar(pd.Timestamp("2024-01-02 09:15"), 100_000)
    rm.on_trade_closed(-2_500, 5)
    ok, why = rm.can_enter(6, 97_500, "long")
    assert not ok and why == "daily_loss_limit"


def test_drawdown_kill_switch_halts_the_run():
    rm = RiskManager(RiskConfig(max_drawdown_pct=10.0), 100_000)
    rm.on_bar(pd.Timestamp("2024-01-02 09:15"), 100_000)
    rm.on_bar(pd.Timestamp("2024-01-03 09:15"), 89_000)
    assert rm.state.halted
    assert not rm.can_enter(1, 89_000, "long")[0]


# --------------------------------------------------------------------- engine
class BuyEveryTenth(Strategy):
    name = "test_buy_every_tenth"
    intraday = False

    @classmethod
    def params(cls):
        return [Param("hold", 3, "int", 1, 10, 1)]

    def prepare(self, df, inst):
        return {"atr": np.full(len(df), 1.0)}

    def warmup(self):
        return 2

    def on_bar(self, ctx):
        if ctx.in_position:
            return Exit("time") if ctx.bars_since_entry >= self.p["hold"] else None
        return Signal("long", stop_atr_mult=2.0) if ctx.i % 10 == 0 else None


class Cheater(Strategy):
    """Tries to trade the bar it is standing on. The Context must not let it."""
    name = "test_cheater"
    intraday = False

    def prepare(self, df, inst):
        return {"atr": np.full(len(df), 1.0)}

    def warmup(self):
        return 2

    def on_bar(self, ctx):
        with pytest.raises(IndexError):
            ctx.close(-1)
        return None


def _trending(n=300):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + np.abs(rng.normal(0.5, 0.2, n))
    low = close - np.abs(rng.normal(0.5, 0.2, n))
    open_ = np.r_[close[0], close[:-1]]
    return bars(np.c_[open_, high, low, close], freq="1D")


def test_entry_fills_at_the_next_open_never_the_signal_close():
    df = _trending()
    r = run_backtest(df, BuyEveryTenth(), INST, EngineConfig(starting_equity=100_000))
    assert r.trades
    for t in r.trades:
        i = df.index.get_loc(t.entry_time)
        assert t.entry_price == pytest.approx(df["open"].iloc[i], abs=0.06)
        assert t.entry_time > df.index[0]


def test_future_indexing_is_refused():
    run_backtest(_trending(120), Cheater(), INST, EngineConfig())


def test_equity_curve_matches_realised_plus_open_pnl():
    df = _trending()
    r = run_backtest(df, BuyEveryTenth(), INST, EngineConfig(starting_equity=100_000))
    realised = sum(t.pnl for t in r.trades)
    assert float(r.equity.iloc[-1]) == pytest.approx(100_000 + realised, abs=1e-6)


def test_shuffled_future_cannot_change_past_trades():
    """Replace the last third of the data with noise. Trades that closed before
    that point must be bit-identical -- the only real proof of no lookahead."""
    df = _trending(360)
    base = run_backtest(df, BuyEveryTenth(), INST, EngineConfig())
    cut = df.index[240]
    alt = df.copy()
    rng = np.random.default_rng(99)
    alt.iloc[240:] = alt.iloc[240:] * (1 + rng.normal(0, 0.05, alt.iloc[240:].shape))
    other = run_backtest(alt, BuyEveryTenth(), INST, EngineConfig())
    a = [t.to_dict() for t in base.trades if t.exit_time < cut]
    b = [t.to_dict() for t in other.trades if t.exit_time < cut]
    assert a and a == b


def test_intraday_strategy_never_holds_overnight():
    from app.strategies.registry import build
    idx = pd.date_range("2024-01-02 09:15", periods=26 * 10, freq="15min")
    idx = idx[[t.time() >= time(9, 15) and t.time() <= time(15, 30) for t in idx]]
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 0.4, len(idx)))
    df = pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3,
                       "close": close, "volume": 1000.0}, index=idx)
    r = run_backtest(df, build("opening_range_breakout"), INST, EngineConfig())
    for t in r.trades:
        assert t.entry_time.date() == t.exit_time.date()


def test_metrics_on_a_known_two_trade_run():
    from app.engine.broker import Trade
    ts = pd.Timestamp("2024-01-02")
    trades = [
        Trade("long", 1, ts, 100, ts, 110, 10, 0, 10, 2.0, 5, "target"),
        Trade("long", 1, ts, 100, ts, 95, -5, 0, -5, -1.0, 3, "stop"),
    ]
    eq = pd.Series([100_000, 100_010, 100_005],
                   index=pd.date_range("2024-01-02", periods=3, freq="D"))
    price = pd.DataFrame({"close": [100.0, 101, 102]}, index=eq.index)
    s = compute(trades, eq, price, 100_000)
    assert s["trades"] == 2
    assert s["win_rate_pct"] == 50.0
    assert s["profit_factor"] == 2.0
    assert s["expectancy_r"] == pytest.approx(0.5)
    assert s["net_profit"] == 5.0
    assert any("Only 2 trades" in w["message"] for w in s["warnings"])


def test_every_registered_strategy_runs_clean():
    from app.strategies.registry import ALL
    idx = pd.date_range("2024-01-02 09:15", periods=1200, freq="15min")
    idx = idx[[time(9, 15) <= t.time() <= time(15, 30) for t in idx]]
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0.01, 0.5, len(idx)))
    df = pd.DataFrame({"open": close, "high": close + np.abs(rng.normal(0.3, 0.1, len(idx))),
                       "low": close - np.abs(rng.normal(0.3, 0.1, len(idx))),
                       "close": close, "volume": rng.integers(1e3, 1e5, len(idx)).astype(float)},
                      index=idx)
    for cls in ALL:
        r = run_backtest(df, cls(), INST, EngineConfig())
        s = compute(r.trades, r.equity, r.price, r.starting_equity)
        assert r.equity.notna().all(), f"{cls.name} produced NaN equity"
        assert s["trades"] >= 0
