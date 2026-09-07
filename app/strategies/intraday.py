"""Intraday strategies. All of them flatten before the session close, so
overnight gap risk -- the largest single risk an intraday account carries --
is never taken.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine import indicators as ta
from app.settings import Instrument
from app.strategies.base import Context, Exit, Param, Signal, Strategy


class OpeningRangeBreakout(Strategy):
    name = "opening_range_breakout"
    intraday = True
    description = """
    Mark the high and low of the first N minutes of the session; trade the first
    break of that range, once per side per day, with an ATR stop and an R-based
    target.

    Two filters do most of the work: the range must not be unusually wide (a huge
    opening range means the move already happened), and breakouts are refused
    after a cut-off time, because a 2pm 'breakout' is usually the day's last
    liquidity trap.
    """

    @classmethod
    def params(cls):
        return [
            Param("range_bars", 3, "int", 1, 12, 1, help="bars forming the opening range"),
            Param("atr_len", 14, "int", 7, 28, 7),
            Param("stop_atr", 1.5, "float", 0.5, 4.0, 0.5),
            Param("target_r", 2.0, "float", 0.5, 6.0, 0.5),
            Param("trail_atr", 0.0, "float", 0.0, 5.0, 0.5),
            Param("breakeven_at_r", 1.0, "float", 0.0, 3.0, 0.5),
            Param("buffer_atr", 0.1, "float", 0.0, 0.5, 0.05,
                  help="how far beyond the range price must close"),
            Param("max_range_atr", 3.0, "float", 1.0, 8.0, 0.5,
                  help="skip the day if the opening range is wider than this many ATRs"),
            Param("cutoff_minutes", 150, "int", 30, 330, 30,
                  help="no new entries this many minutes after the open"),
            Param("one_trade_per_day", True, "bool"),
            Param("allow_short", True, "bool"),
        ]

    def prepare(self, df, inst):
        p = self.p
        day = pd.Series(df.index.date, index=df.index)
        bar_of_day = df.groupby(day).cumcount().to_numpy()
        n = p["range_bars"]
        # Opening range = extremes of the first n bars, available only AFTER
        # those bars have closed. Bars inside the range get NaN so nothing can
        # trade the range while it is still forming.
        hi = df["high"].groupby(day).transform(lambda s: s.rolling(n, min_periods=n).max().iloc[n - 1]
                                               if len(s) >= n else np.nan)
        lo = df["low"].groupby(day).transform(lambda s: s.rolling(n, min_periods=n).min().iloc[n - 1]
                                              if len(s) >= n else np.nan)
        hi = hi.to_numpy().astype("float64").copy()
        lo = lo.to_numpy().astype("float64").copy()
        forming = bar_of_day < n
        hi[forming] = np.nan
        lo[forming] = np.nan
        mins = np.asarray([t.hour * 60 + t.minute for t in df.index.time], dtype="float64")
        open_min = float(inst.session_open.hour * 60 + inst.session_open.minute)
        return {"or_hi": hi, "or_lo": lo,
                "atr": ta.atr(df["high"], df["low"], df["close"], p["atr_len"]),
                "since_open": mins - open_min,
                "bar_of_day": bar_of_day.astype("float64")}

    def warmup(self) -> int:
        return self.p["atr_len"] * 3

    def on_bar(self, ctx: Context):
        p = self.p
        if ctx.is_new_day:
            self._traded_today = 0
        if not ctx.ready("or_hi", "or_lo", "atr"):
            return None

        if ctx.in_position:
            if p["breakeven_at_r"] > 0 and ctx.position.initial_stop is not None:
                risk = abs(ctx.position.entry_price - ctx.position.initial_stop)
                move = (ctx.close(0) - ctx.position.entry_price) * ctx.position.dir
                if risk > 0 and move >= p["breakeven_at_r"] * risk:
                    ctx.request_breakeven = True
            return None

        if p["one_trade_per_day"] and getattr(self, "_traded_today", 0) >= 1:
            return None
        if ctx.since_open(0) > p["cutoff_minutes"] or ctx.minutes_to_close < 20:
            return None

        atr = ctx.atr(0)
        rng = ctx.or_hi(0) - ctx.or_lo(0)
        if atr <= 0 or rng <= 0 or rng > p["max_range_atr"] * atr:
            return None

        buf = p["buffer_atr"] * atr
        if ctx.close(0) > ctx.or_hi(0) + buf:
            self._traded_today = getattr(self, "_traded_today", 0) + 1
            return Signal("long", stop_atr_mult=p["stop_atr"], target_r=p["target_r"],
                          trail_atr_mult=p["trail_atr"] or None, reason="or_break_up")
        if p["allow_short"] and ctx.close(0) < ctx.or_lo(0) - buf:
            self._traded_today = getattr(self, "_traded_today", 0) + 1
            return Signal("short", stop_atr_mult=p["stop_atr"], target_r=p["target_r"],
                          trail_atr_mult=p["trail_atr"] or None, reason="or_break_down")
        return None


class VwapPullback(Strategy):
    name = "vwap_pullback"
    intraday = True
    description = """
    Trade with the day's direction, entering on a pullback to session VWAP rather
    than chasing the move. Direction comes from price sitting on the correct side
    of VWAP with a rising/falling EMA; the entry trigger is a rejection candle at
    the band.

    Pullback entries have a much better average entry price than breakout entries,
    which is the whole point: the same signal with a tighter stop risks less money
    for the same target.
    """

    @classmethod
    def params(cls):
        return [
            Param("ema_len", 20, "int", 5, 60, 5),
            Param("atr_len", 14, "int", 7, 28, 7),
            Param("band_atr", 0.5, "float", 0.1, 2.0, 0.1,
                  help="how close to VWAP counts as a pullback"),
            Param("stop_atr", 1.5, "float", 0.5, 4.0, 0.5),
            Param("target_r", 2.0, "float", 0.5, 5.0, 0.5),
            Param("trail_atr", 2.0, "float", 0.0, 5.0, 0.5),
            Param("min_trend_atr", 0.5, "float", 0.0, 3.0, 0.25,
                  help="price must be this far from VWAP earlier to prove a trend"),
            Param("start_minutes", 30, "int", 0, 180, 15, help="wait for VWAP to settle"),
            Param("cutoff_minutes", 240, "int", 60, 360, 30),
            Param("allow_short", True, "bool"),
        ]

    def prepare(self, df, inst):
        p = self.p
        vwap = ta.vwap_session(df)
        atr = ta.atr(df["high"], df["low"], df["close"], p["atr_len"])
        mins = np.asarray([t.hour * 60 + t.minute for t in df.index.time], dtype="float64")
        open_min = float(inst.session_open.hour * 60 + inst.session_open.minute)
        dist = (df["close"].to_numpy() - vwap) / np.where(atr > 0, atr, np.nan)
        return {"vwap": vwap, "atr": atr, "ema": ta.ema(df["close"], p["ema_len"]),
                "since_open": mins - open_min, "vwap_dist": dist,
                "max_dist": ta.rolling_max(pd.Series(dist), 10),
                "min_dist": ta.rolling_min(pd.Series(dist), 10)}

    def warmup(self) -> int:
        return max(self.p["ema_len"], self.p["atr_len"]) * 3

    def on_bar(self, ctx: Context):
        p = self.p
        if ctx.in_position or not ctx.ready("vwap", "atr", "ema", "vwap_dist"):
            return None
        if ctx.since_open(0) < p["start_minutes"] or ctx.since_open(0) > p["cutoff_minutes"]:
            return None
        if ctx.minutes_to_close < 20:
            return None

        d = ctx.vwap_dist(0)
        long_ok = (ctx.close(0) > ctx.vwap(0) and ctx.ema.rising(3)
                   and ctx.max_dist(0) >= p["min_trend_atr"])
        short_ok = (ctx.close(0) < ctx.vwap(0) and ctx.ema.falling(3)
                    and ctx.min_dist(0) <= -p["min_trend_atr"])
        # A pullback that closed back up = the band held.
        held_up = ctx.close(0) > ctx.open(0) and ctx.low(0) <= ctx.vwap(0) + p["band_atr"] * ctx.atr(0)
        held_dn = ctx.close(0) < ctx.open(0) and ctx.high(0) >= ctx.vwap(0) - p["band_atr"] * ctx.atr(0)

        if long_ok and held_up and 0 <= d <= p["band_atr"] * 2:
            return Signal("long", stop_atr_mult=p["stop_atr"], target_r=p["target_r"],
                          trail_atr_mult=p["trail_atr"] or None, reason="vwap_pullback_long")
        if p["allow_short"] and short_ok and held_dn and -p["band_atr"] * 2 <= d <= 0:
            return Signal("short", stop_atr_mult=p["stop_atr"], target_r=p["target_r"],
                          trail_atr_mult=p["trail_atr"] or None, reason="vwap_pullback_short")
        return None


class BollingerMeanReversion(Strategy):
    name = "bollinger_mean_reversion"
    intraday = True
    description = """
    Fade stretched moves back to the mean: buy a close below the lower band with
    RSI oversold, exit at the middle band. Mean reversion wins often and loses
    big, so the ATR stop here is not optional -- it is the entire risk model.

    Only trades when ADX is LOW. Running this in a trend is how mean-reversion
    accounts die.
    """

    @classmethod
    def params(cls):
        return [
            Param("bb_len", 20, "int", 10, 50, 5),
            Param("bb_k", 2.0, "float", 1.0, 3.5, 0.25),
            Param("rsi_len", 14, "int", 5, 28, 7),
            Param("rsi_low", 30.0, "float", 10, 45, 5),
            Param("rsi_high", 70.0, "float", 55, 90, 5),
            Param("adx_len", 14, "int", 7, 28, 7),
            Param("adx_max", 25.0, "float", 10, 45, 5, help="skip entries above this"),
            Param("atr_len", 14, "int", 7, 28, 7),
            Param("stop_atr", 2.0, "float", 1.0, 4.0, 0.5),
            Param("target_r", 0.0, "float", 0.0, 4.0, 0.5, help="0 = exit at the mean instead"),
            Param("max_bars", 20, "int", 0, 80, 10, help="give up if the mean is not reached"),
            Param("allow_short", True, "bool"),
        ]

    def prepare(self, df, inst):
        p = self.p
        mid, up, lo = ta.bollinger(df["close"], p["bb_len"], p["bb_k"])
        return {"bb_mid": mid, "bb_up": up, "bb_lo": lo,
                "rsi": ta.rsi(df["close"], p["rsi_len"]),
                "adx": ta.adx(df["high"], df["low"], df["close"], p["adx_len"]),
                "atr": ta.atr(df["high"], df["low"], df["close"], p["atr_len"])}

    def warmup(self) -> int:
        return max(self.p["bb_len"], self.p["adx_len"], self.p["atr_len"]) * 3

    def on_bar(self, ctx: Context):
        p = self.p
        if not ctx.ready("bb_mid", "rsi", "adx", "atr"):
            return None

        if ctx.in_position:
            if p["max_bars"] and ctx.bars_since_entry >= p["max_bars"]:
                return Exit("mean_not_reached")
            if ctx.is_long and ctx.close(0) >= ctx.bb_mid(0):
                return Exit("reverted_to_mean")
            if ctx.is_short and ctx.close(0) <= ctx.bb_mid(0):
                return Exit("reverted_to_mean")
            return None

        if ctx.adx(0) > p["adx_max"] or ctx.minutes_to_close < 25:
            return None
        if ctx.close(0) < ctx.bb_lo(0) and ctx.rsi(0) < p["rsi_low"]:
            return Signal("long", stop_atr_mult=p["stop_atr"],
                          target_r=p["target_r"] or None, reason="band_oversold")
        if p["allow_short"] and ctx.close(0) > ctx.bb_up(0) and ctx.rsi(0) > p["rsi_high"]:
            return Signal("short", stop_atr_mult=p["stop_atr"],
                          target_r=p["target_r"] or None, reason="band_overbought")
        return None
