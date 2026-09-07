"""Vectorised indicators. Computed once over the full series before the bar
loop starts; the loop then only ever reads index <= i, so precomputing is
safe and roughly two orders of magnitude faster than per-bar recalculation.

Every function returns a float array the same length as the input, NaN-padded
at the front for the warm-up period. NaN is meaningful here -- the engine
refuses to trade while any indicator the strategy declared is still NaN.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _s(x) -> pd.Series:
    return x if isinstance(x, pd.Series) else pd.Series(np.asarray(x, dtype="float64"))


def sma(x, n: int) -> np.ndarray:
    return _s(x).rolling(n, min_periods=n).mean().to_numpy()


def ema(x, n: int) -> np.ndarray:
    s = _s(x)
    out = s.ewm(span=n, adjust=False).mean()
    out.iloc[: n - 1] = np.nan            # don't pretend the first bars are warm
    return out.to_numpy()


def rma(x, n: int) -> np.ndarray:
    """Wilder's smoothing -- what RSI/ATR/ADX are actually defined with."""
    s = _s(x)
    out = s.ewm(alpha=1.0 / n, adjust=False).mean()
    out.iloc[: n - 1] = np.nan
    return out.to_numpy()


def rsi(close, n: int = 14) -> np.ndarray:
    c = _s(close)
    d = c.diff()
    gain = rma(d.clip(lower=0.0), n)
    loss = rma((-d).clip(lower=0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(loss == 0, np.inf, gain / loss)
    return 100.0 - 100.0 / (1.0 + rs)


def true_range(high, low, close) -> np.ndarray:
    h, l, c = _s(high), _s(low), _s(close)
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).to_numpy()


def atr(high, low, close, n: int = 14) -> np.ndarray:
    return rma(true_range(high, low, close), n)


def adx(high, low, close, n: int = 14) -> np.ndarray:
    """Trend strength. Above ~20-25 means a trend worth following; below it,
    breakout systems get chopped to death."""
    h, l = _s(high), _s(low)
    up, dn = h.diff(), -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = rma(true_range(high, low, close), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * rma(plus, n) / tr
        mdi = 100.0 * rma(minus, n) / tr
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    return rma(np.nan_to_num(dx, nan=np.nan), n)


def bollinger(close, n: int = 20, k: float = 2.0):
    c = _s(close)
    mid = c.rolling(n, min_periods=n).mean()
    sd = c.rolling(n, min_periods=n).std(ddof=0)
    return mid.to_numpy(), (mid + k * sd).to_numpy(), (mid - k * sd).to_numpy()


def donchian(high, low, n: int = 20):
    """Prior-N-bar channel. Shifted by one bar so the current bar's own high
    can never form the level it is supposed to break -- a classic lookahead
    bug that makes breakout backtests look magical."""
    hi = _s(high).rolling(n, min_periods=n).max().shift(1)
    lo = _s(low).rolling(n, min_periods=n).min().shift(1)
    return hi.to_numpy(), lo.to_numpy()


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def stdev(x, n: int) -> np.ndarray:
    return _s(x).rolling(n, min_periods=n).std(ddof=0).to_numpy()


def rolling_max(x, n: int) -> np.ndarray:
    return _s(x).rolling(n, min_periods=n).max().to_numpy()


def rolling_min(x, n: int) -> np.ndarray:
    return _s(x).rolling(n, min_periods=n).min().to_numpy()


def slope(x, n: int) -> np.ndarray:
    """Per-bar change of an N-bar linear fit, normalised by price."""
    s = _s(x)
    return ((s - s.shift(n)) / n / s.replace(0, np.nan)).to_numpy()


def zscore(x, n: int) -> np.ndarray:
    s = _s(x)
    m = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return ((s - m) / sd.replace(0, np.nan)).to_numpy()


def vwap_session(df: pd.DataFrame) -> np.ndarray:
    """Session-anchored VWAP; resets each calendar day. Falls back to a
    typical-price running mean when the feed has no volume."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].fillna(0.0)
    day = pd.Series(df.index.date, index=df.index)
    if float(vol.sum()) <= 0:
        return tp.groupby(day).expanding().mean().reset_index(level=0, drop=True).to_numpy()
    pv = (tp * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum().replace(0, np.nan)
    return (pv / cv).to_numpy()
