"""Download historical bars into data/*.csv.

    python -m app.data.seed --symbols RELIANCE TCS --timeframe 15m
    python -m app.data.seed --all --timeframe 1d --period 10y

Yahoo caps intraday history (~60 days for <1h, ~730 days for 1h), so daily
data goes back years while 5m/15m gives you a couple of months. For serious
intraday work, export CSV from your broker or MT5 and drop it in data/ as
SYMBOL_TF.csv -- the loader reads MT5 and generic exports directly.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from app.data.loader import path_for
from app.settings import YF_TICKERS

YF_INTERVAL = {"1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
               "1h": "60m", "1d": "1d", "1w": "1wk"}
MAX_PERIOD = {"1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d", "30m": "60d",
              "1h": "730d", "1d": "max", "1w": "max"}


def fetch(symbol: str, timeframe: str, period: str | None) -> pd.DataFrame:
    import yfinance as yf

    ticker = YF_TICKERS.get(symbol.upper(), symbol.upper())
    interval = YF_INTERVAL.get(timeframe)
    if interval is None:
        raise SystemExit(f"timeframe {timeframe} not supported by the Yahoo seeder")
    period = period or MAX_PERIOD[timeframe]
    df = yf.download(ticker, period=period, interval=interval,
                     auto_adjust=False, progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty:
        raise RuntimeError(f"Yahoo returned nothing for {ticker} @ {interval}/{period}")
    df = df.rename(columns=str.lower).reset_index()
    df = df.rename(columns={df.columns[0]: "timestamp"})
    keep = ["timestamp", "open", "high", "low", "close", "volume"]
    return df[[c for c in keep if c in df.columns]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed local CSV data from Yahoo Finance")
    ap.add_argument("--symbols", nargs="*", default=[])
    ap.add_argument("--all", action="store_true", help="every symbol in settings")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--period", default=None, help="e.g. 60d, 2y, max")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    a = ap.parse_args(argv)

    symbols = list(YF_TICKERS) if a.all else [s.upper() for s in a.symbols]
    if not symbols:
        ap.error("give --symbols or --all")

    ok = failed = 0
    for sym in symbols:
        out = path_for(sym, a.timeframe)
        if out.exists() and not a.force:
            print(f"  skip  {sym:<12} {a.timeframe}  (exists; --force to overwrite)")
            continue
        try:
            df = fetch(sym, a.timeframe, a.period)
            df.to_csv(out, index=False)
            print(f"  ok    {sym:<12} {a.timeframe}  {len(df):>6} bars  "
                  f"{df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
            ok += 1
        except Exception as e:                      # network/ticker issues are routine
            print(f"  FAIL  {sym:<12} {a.timeframe}  {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{ok} written, {failed} failed -> {path_for('X', 'Y').parent}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
