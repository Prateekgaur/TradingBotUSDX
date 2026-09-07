"""Trend-following strategies.

These lose more often than they win. They make money because the winners are
allowed to run several times the size of the losers -- which only works if the
stop is never widened and the target is not taken too early. Both rules are
enforced by the engine, not left to nerve.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine import indicators as ta
from app.settings import Instrument
from app.strategies.base import Context, Exit, Param, Signal, Strategy


class EmaTrendATR(Strategy):
    name = "ema_trend_atr"
    intraday = True
    description = """
    EMA crossover, taken only in the direction of a slower trend filter and only
    when ADX says a trend actually exists. Stop is a multiple of ATR, so position
    size shrinks automatically when the market gets violent. Optional break-even
    move once the trade is up 1R, then an ATR trail.

    Chop is what kills crossover systems; the ADX floor and the trend filter exist
    to sit out that chop rather than to catch more moves.
    """

    @classmethod
    def params(cls):
        return [
            Param("fast", 9, "int", 3, 30, 1, help="fast EMA length"),
            Param("slow", 21, "int", 10, 80, 5, help="slow EMA length"),
            Param("trend", 100, "int", 20, 300, 20, help="regime filter EMA; 0 disables"),
            Param("adx_len", 14, "int", 7, 28, 7),
            Param("adx_min", 20.0, "float", 0, 35, 5, help="skip entries below this trend strength"),
            Param("atr_len", 14, "int", 7, 28, 7),
            Param("stop_atr", 2.0, "float", 1.0, 4.0, 0.5, help="stop distance in ATRs"),
            Param("target_r", 2.5, "float", 0.0, 6.0, 0.5, help="target in R; 0 = no fixed target"),
            Param("trail_atr", 3.0, "float", 0.0, 6.0, 0.5, help="ATR trail; 0 disables"),
            Param("breakeven_at_r", 1.0, "float", 0.0, 3.0, 0.5, help="move stop to entry at this R"),
            Param("min_atr_pct", 0.15, "float", 0.0, 1.0, 0.05,
                  help="skip when ATR is below this %% of price (dead market)"),
            Param("no_trade_first_bars", 2, "int", 0, 12, 2, help="skip the opening auction noise"),
            Param("allow_short", True, "bool"),
        ]

    def prepare(self, df: pd.DataFrame, inst: Instrument) -> dict[str, np.ndarray]:
        p, c = self.p, df["close"]
        out = {
            "ema_fast": ta.ema(c, p["fast"]),
            "ema_slow": ta.ema(c, p["slow"]),
            "atr": ta.atr(df["high"], df["low"], c, p["atr_len"]),
            "adx": ta.adx(df["high"], df["low"], c, p["adx_len"]),
        }
        out["ema_trend"] = ta.ema(c, p["trend"]) if p["trend"] else np.zeros(len(df))
        out["atr_pct"] = out["atr"] / c.to_numpy() * 100.0
        return out

    def warmup(self) -> int:
        return max(self.p["slow"], self.p["trend"], self.p["atr_len"]) * 3 + 5

    def on_bar(self, ctx: Context):
        p = self.p
        if not ctx.ready("ema_fast", "ema_slow", "atr", "adx"):
            return None

        if ctx.in_position:
            # Break-even protection: once the trade has paid for its own risk,
            # stop letting it become a loser.
            if p["breakeven_at_r"] > 0 and ctx.position.initial_stop is not None:
                risk = abs(ctx.position.entry_price - ctx.position.initial_stop)
                move = (ctx.close(0) - ctx.position.entry_price) * ctx.position.dir
                if risk > 0 and move >= p["breakeven_at_r"] * risk:
                    ctx.request_breakeven = True
            if ctx.is_long and ctx.ema_fast.crossed_below(ctx.ema_slow):
                return Exit("trend_flip")
            if ctx.is_short and ctx.ema_fast.crossed_above(ctx.ema_slow):
                return Exit("trend_flip")
            return None

        if ctx.bar_of_day < p["no_trade_first_bars"]:
            return None
        if ctx.minutes_to_close < 20:
            return None                       # no fresh risk into the square-off
        if p["adx_min"] and ctx.adx(0) < p["adx_min"]:
            return None
        if p["min_atr_pct"] and ctx.atr_pct(0) < p["min_atr_pct"]:
            return None

        up = ctx.close(0) > ctx.ema_trend(0) if p["trend"] else True
        dn = ctx.close(0) < ctx.ema_trend(0) if p["trend"] else True

        if ctx.ema_fast.crossed_above(ctx.ema_slow) and up:
            return Signal("long", stop_atr_mult=p["stop_atr"],
                          target_r=p["target_r"] or None,
                          trail_atr_mult=p["trail_atr"] or None, reason="ema_cross_up")
        if p["allow_short"] and ctx.ema_fast.crossed_below(ctx.ema_slow) and dn:
            return Signal("short", stop_atr_mult=p["stop_atr"],
                          target_r=p["target_r"] or None,
                          trail_atr_mult=p["trail_atr"] or None, reason="ema_cross_down")
        return None


class DonchianBreakout(Strategy):
    name = "donchian_breakout"
    intraday = False
    description = """
    Turtle-style channel breakout: buy the highest high of the last N bars, exit
    on the opposite M-bar channel or an ATR stop. Built for daily bars and
    trending instruments.

    The channel is shifted one bar so today's own high cannot create the level it
    is breaking -- the classic way this system backtests better than it trades.
    """

    @classmethod
    def params(cls):
        return [
            Param("entry_len", 20, "int", 10, 100, 10),
            Param("exit_len", 10, "int", 5, 50, 5),
            Param("atr_len", 14, "int", 7, 28, 7),
            Param("stop_atr", 2.5, "float", 1.0, 5.0, 0.5),
            Param("target_r", 0.0, "float", 0.0, 6.0, 1.0, help="0 = let the trail decide"),
            Param("trail_atr", 3.0, "float", 0.0, 6.0, 0.5),
            Param("trend_len", 200, "int", 0, 300, 50, help="only trade with the long trend; 0 disables"),
            Param("allow_short", True, "bool"),
        ]

    def prepare(self, df, inst):
        p = self.p
        hi, lo = ta.donchian(df["high"], df["low"], p["entry_len"])
        xhi, xlo = ta.donchian(df["high"], df["low"], p["exit_len"])
        return {
            "dc_hi": hi, "dc_lo": lo, "x_hi": xhi, "x_lo": xlo,
            "atr": ta.atr(df["high"], df["low"], df["close"], p["atr_len"]),
            "trend": ta.ema(df["close"], p["trend_len"]) if p["trend_len"]
                     else np.zeros(len(df)),
        }

    def warmup(self) -> int:
        return max(self.p["entry_len"], self.p["trend_len"], self.p["atr_len"]) * 2 + 5

    def on_bar(self, ctx: Context):
        p = self.p
        if not ctx.ready("dc_hi", "dc_lo", "atr"):
            return None
        if ctx.in_position:
            if ctx.is_long and ctx.close(0) < ctx.x_lo(0):
                return Exit("channel_exit")
            if ctx.is_short and ctx.close(0) > ctx.x_hi(0):
                return Exit("channel_exit")
            return None

        up = ctx.close(0) > ctx.trend(0) if p["trend_len"] else True
        dn = ctx.close(0) < ctx.trend(0) if p["trend_len"] else True
        if ctx.close(0) > ctx.dc_hi(0) and up:
            return Signal("long", stop_atr_mult=p["stop_atr"], target_r=p["target_r"] or None,
                          trail_atr_mult=p["trail_atr"] or None, reason="breakout_high")
        if p["allow_short"] and ctx.close(0) < ctx.dc_lo(0) and dn:
            return Signal("short", stop_atr_mult=p["stop_atr"], target_r=p["target_r"] or None,
                          trail_atr_mult=p["trail_atr"] or None, reason="breakout_low")
        return None
