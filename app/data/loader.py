"""CSV -> validated OHLCV DataFrame.

Every backtest reads from local CSV only. Network fetches happen in `seed.py`
and write CSV; the engine never touches the network. That makes any run
reproducible months later, which matters when you are comparing a strategy
change against a result you recorded in June.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.settings import DATA_DIR

REQUIRED = ["open", "high", "low", "close"]

_ALIASES = {
    "date": "timestamp", "datetime": "timestamp", "time": "timestamp",
    "<date>": "timestamp", "<time>": "timestamp", "gmt time": "timestamp",
    "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
    "<open>": "open", "<high>": "high", "<low>": "low", "<close>": "close",
    "<tickvol>": "volume", "<vol>": "volume", "tickvol": "volume",
    "adj close": "adj_close", "vol.": "volume", "price": "close",
}

TF_PANDAS = {
    "1m": "1min", "2m": "2min", "3m": "3min", "5m": "5min", "10m": "10min",
    "15m": "15min", "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h",
    "1d": "1D", "1w": "1W",
}


@dataclass
class Dataset:
    symbol: str
    timeframe: str
    df: pd.DataFrame
    source: str
    warnings: list[str]

    @property
    def start(self) -> pd.Timestamp:
        return self.df.index[0]

    @property
    def end(self) -> pd.Timestamp:
        return self.df.index[-1]


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = {}
    for c in df.columns:
        key = str(c).strip().lower()
        cols[c] = _ALIASES.get(key, key.replace(" ", "_"))
    df = df.rename(columns=cols)
    # MT5 exports split date and time into two columns.
    if "timestamp" not in df.columns and {"date", "time"} <= set(df.columns):
        df["timestamp"] = df["date"].astype(str) + " " + df["time"].astype(str)
    return df.loc[:, ~df.columns.duplicated()]


def path_for(symbol: str, timeframe: str) -> Path:
    return DATA_DIR / f"{symbol.upper()}_{timeframe}.csv"


def available() -> list[dict]:
    """Every dataset sitting in data/, newest file first."""
    out = []
    for p in sorted(DATA_DIR.glob("*.csv"), key=lambda p: -p.stat().st_mtime):
        m = re.match(r"^(?P<sym>.+)_(?P<tf>\d+[mhdwMHDW]|1w)\.csv$", p.name)
        if not m:
            continue
        try:
            head = pd.read_csv(p, nrows=1)
            n = sum(1 for _ in open(p, "rb")) - 1
        except Exception:
            continue
        out.append({
            "symbol": m.group("sym").upper(),
            "timeframe": m.group("tf").lower(),
            "bars": max(n, 0),
            "file": p.name,
            "columns": [str(c) for c in head.columns],
        })
    return out


def load(symbol: str, timeframe: str, start: str | None = None,
         end: str | None = None, resample_from: str | None = None) -> Dataset:
    """Load one dataset, validating hard enough that silent garbage cannot
    reach the engine. Bad data is the most common cause of a backtest that
    looks brilliant and trades terribly."""
    warnings: list[str] = []
    p = path_for(symbol, timeframe)
    source_tf = timeframe

    if not p.exists():
        # Fall back to resampling a finer timeframe we do have.
        cand = resample_from or _finest_available(symbol, timeframe)
        if cand is None:
            raise FileNotFoundError(
                f"No data for {symbol} {timeframe}. Expected {p}. "
                f"Run:  python -m app.data.seed --symbols {symbol} --timeframe {timeframe}")
        p = path_for(symbol, cand)
        source_tf = cand
        warnings.append(f"{timeframe} not on disk; resampled from {cand}")

    df = _normalise_columns(pd.read_csv(p))
    if "timestamp" not in df.columns:
        raise ValueError(f"{p.name}: no timestamp/date column found (got {list(df.columns)})")
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{p.name}: missing required columns {missing}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=False, format="mixed")
    if getattr(df["timestamp"].dtype, "tz", None) is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)

    n0 = len(df)
    df = df.dropna(subset=["timestamp", *REQUIRED])
    if "volume" not in df.columns:
        df["volume"] = np.nan
    df = df[["timestamp", *REQUIRED, "volume"]].astype(
        {c: "float64" for c in [*REQUIRED, "volume"]}, errors="ignore")

    df = df.sort_values("timestamp")
    dupes = int(df["timestamp"].duplicated().sum())
    if dupes:
        df = df.drop_duplicates("timestamp", keep="last")
        warnings.append(f"dropped {dupes} duplicate timestamps")

    # Structural sanity: high must bound the bar, low must floor it.
    bad = (df["high"] < df["low"]) | (df["high"] < df[["open", "close"]].max(axis=1) - 1e-9) \
          | (df["low"] > df[["open", "close"]].min(axis=1) + 1e-9)
    if int(bad.sum()):
        warnings.append(f"dropped {int(bad.sum())} bars with impossible OHLC")
        df = df[~bad]
    nonpos = (df[REQUIRED] <= 0).any(axis=1)
    if int(nonpos.sum()):
        warnings.append(f"dropped {int(nonpos.sum())} bars with non-positive prices")
        df = df[~nonpos]

    df = df.set_index("timestamp")
    if source_tf != timeframe:
        df = resample(df, timeframe)

    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end) + pd.Timedelta(days=1)]

    if len(df) < 50:
        raise ValueError(f"{symbol} {timeframe}: only {len(df)} usable bars after cleaning "
                         f"-- too few to test anything.")
    if n0 - len(df) > n0 * 0.05:
        warnings.append(f"cleaning removed {n0 - len(df)}/{n0} rows ({(n0-len(df))/n0:.0%}) "
                        f"-- inspect this file before trusting results")

    # Flag suspicious gaps. Overnight and weekend breaks are normal for
    # intraday data, so only gaps WITHIN a session count as missing bars.
    if len(df) > 10:
        ts = pd.Series(df.index)
        deltas = ts.diff()
        same_day = ts.dt.date.eq(ts.dt.date.shift(1))
        med = deltas[same_day].median()
        if pd.notna(med) and med < pd.Timedelta("1D"):
            big = int(((deltas > med * 10) & same_day).sum())
            if big:
                warnings.append(f"{big} gaps inside a session larger than 10x the "
                                f"normal bar interval -- bars are missing")

    return Dataset(symbol.upper(), timeframe, df, str(p.name), warnings)


def _finest_available(symbol: str, wanted: str) -> str | None:
    order = list(TF_PANDAS.keys())
    if wanted not in order:
        return None
    want_i = order.index(wanted)
    for tf in order[:want_i]:          # only ever upsample from finer data
        if path_for(symbol, tf).exists():
            return tf
    return None


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    rule = TF_PANDAS.get(timeframe)
    if rule is None:
        raise ValueError(f"Unsupported timeframe {timeframe}")
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["open", "high", "low", "close"])
