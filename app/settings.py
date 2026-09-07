"""Instrument definitions and global paths.

An `Instrument` carries everything the paper broker needs to price a fill
realistically: tick size, quantity step, cost model and session hours.
Adding a new market (XAUUSD, crypto, US equities) means adding one entry
here -- the engine itself is instrument-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"
DB_PATH = RUNS_DIR / "runs.sqlite"

DATA_DIR.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    currency: str = "INR"
    tick_size: float = 0.05          # minimum price increment
    qty_step: float = 1.0            # minimum tradable quantity increment
    min_qty: float = 1.0
    point_value: float = 1.0         # P&L per 1.0 price move per 1 qty
    # Cost model. Real costs are the difference between a backtest that
    # "works" and an account that bleeds. Defaults are deliberately harsh.
    commission_pct: float = 0.0003   # 0.03% per side (typical Indian discount broker + fees)
    commission_min: float = 0.0      # absolute floor per side
    slippage_ticks: float = 2.0      # ticks lost per side on market orders
    spread_ticks: float = 0.0        # for OTC/CFD instruments quoted with a spread
    # Session. Used for intraday square-off and for rejecting bars outside RTH.
    session_open: time = time(9, 15)
    session_close: time = time(15, 30)
    square_off: time = time(15, 15)  # intraday strategies flatten here
    timezone: str = "Asia/Kolkata"
    tags: tuple[str, ...] = field(default_factory=tuple)

    def round_price(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 8)

    def round_qty(self, qty: float) -> float:
        if self.qty_step <= 0:
            return qty
        steps = int(abs(qty) / self.qty_step)
        return steps * self.qty_step

    def commission(self, price: float, qty: float) -> float:
        notional = abs(price * qty * self.point_value)
        return max(notional * self.commission_pct, self.commission_min if qty else 0.0)


_NSE = dict(currency="INR", tick_size=0.05, qty_step=1.0, min_qty=1.0,
            commission_pct=0.0003, slippage_ticks=2.0, tags=("equity", "nse"))

INSTRUMENTS: dict[str, Instrument] = {
    "RELIANCE": Instrument("RELIANCE", "Reliance Industries", **_NSE),
    "TCS": Instrument("TCS", "Tata Consultancy Services", **_NSE),
    "HDFCBANK": Instrument("HDFCBANK", "HDFC Bank", **_NSE),
    "INFY": Instrument("INFY", "Infosys", **_NSE),
    "ICICIBANK": Instrument("ICICIBANK", "ICICI Bank", **_NSE),
    "SBIN": Instrument("SBIN", "State Bank of India", **_NSE),
    "TATAMOTORS": Instrument("TATAMOTORS", "Tata Motors", **_NSE),
    "AXISBANK": Instrument("AXISBANK", "Axis Bank", **_NSE),
    "ITC": Instrument("ITC", "ITC Ltd", **_NSE),
    "LT": Instrument("LT", "Larsen & Toubro", **_NSE),
    "NIFTY50": Instrument("NIFTY50", "Nifty 50 Index", **{**_NSE, "tags": ("index", "nse")}),
    "BANKNIFTY": Instrument("BANKNIFTY", "Bank Nifty Index", **{**_NSE, "tags": ("index", "nse")}),
    # Non-INR example: proves the engine is not hard-wired to equities.
    "XAUUSD": Instrument(
        "XAUUSD", "Gold vs US Dollar", currency="USD", tick_size=0.01,
        qty_step=0.01, min_qty=0.01, point_value=100.0,   # 1 lot = 100 oz
        commission_pct=0.0, commission_min=3.5, slippage_ticks=10.0,
        spread_ticks=20.0, session_open=time(0, 0), session_close=time(23, 59),
        square_off=time(23, 55), timezone="UTC", tags=("cfd", "metal"),
    ),
    "AAPL": Instrument("AAPL", "Apple Inc", currency="USD", tick_size=0.01,
                       commission_pct=0.0, commission_min=1.0, slippage_ticks=2.0,
                       session_open=time(9, 30), session_close=time(16, 0),
                       square_off=time(15, 55), timezone="America/New_York",
                       tags=("equity", "us")),
}

# Yahoo Finance ticker mapping for the seeding script.
YF_TICKERS: dict[str, str] = {
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS", "HDFCBANK": "HDFCBANK.NS",
    "INFY": "INFY.NS", "ICICIBANK": "ICICIBANK.NS", "SBIN": "SBIN.NS",
    "TATAMOTORS": "TATAMOTORS.NS", "AXISBANK": "AXISBANK.NS", "ITC": "ITC.NS",
    "LT": "LT.NS", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK",
    "XAUUSD": "GC=F", "AAPL": "AAPL",
}

DEFAULT_INSTRUMENT = Instrument("GENERIC", "Unknown instrument")


def get_instrument(symbol: str) -> Instrument:
    return INSTRUMENTS.get(symbol.upper(), Instrument(symbol.upper(), symbol.upper()))
