"""Strategy API.

A strategy sees the market through `Context`, which exposes only values at or
before the current bar. There is no way to reach forward: `ctx.close(0)` is the
bar that just closed, `ctx.close(1)` the one before it, and any attempt to look
at a future index raises. Lookahead is prevented by construction rather than by
reviewer discipline, which is the only way it stays prevented.

Two methods to implement:

    params()  -> the tunable knobs, with ranges the optimiser can search
    prepare() -> vectorised indicators, computed once over the whole series
    on_bar()  -> return a Signal, an Exit, or None

Exits via stop/target are handled by the broker intrabar; `on_bar` only needs
to handle discretionary exits (signal flipped, time stop, session end).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from app.settings import Instrument

Side = Literal["long", "short"]


@dataclass
class Param:
    name: str
    default: Any
    kind: Literal["int", "float", "bool", "choice"] = "int"
    low: float | None = None
    high: float | None = None
    step: float | None = None
    choices: list | None = None
    help: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "default": self.default, "kind": self.kind,
                "low": self.low, "high": self.high, "step": self.step,
                "choices": self.choices, "help": self.help}

    def grid(self) -> list:
        """Candidate values for a coarse parameter sweep."""
        if self.kind == "bool":
            return [True, False]
        if self.kind == "choice":
            return list(self.choices or [self.default])
        if self.low is None or self.high is None:
            return [self.default]
        step = self.step or (1 if self.kind == "int" else (self.high - self.low) / 4)
        vals = np.arange(self.low, self.high + step / 2, step)
        return [int(v) for v in vals] if self.kind == "int" else [round(float(v), 6) for v in vals]


@dataclass
class Signal:
    """An intent to open a position on the NEXT bar's open.

    Give the stop either as an absolute price or as an ATR multiple; ATR is
    usually better because it adapts the stop -- and therefore the position
    size -- to current volatility.
    """
    side: Side
    stop_price: float | None = None
    stop_atr_mult: float | None = None
    target_price: float | None = None
    target_r: float | None = None          # target at N times the risk distance
    trail_atr_mult: float | None = None
    reason: str = ""
    tag: str = ""


@dataclass
class Exit:
    reason: str = "signal_exit"


class _Series:
    """Read-only view of one precomputed array, clamped to the current bar."""

    __slots__ = ("_a", "_ctx")

    def __init__(self, arr: np.ndarray, ctx: "Context"):
        self._a = arr
        self._ctx = ctx

    def __call__(self, back: int = 0) -> float:
        i = self._ctx.i - back
        if back < 0:
            raise IndexError("looking into the future is not allowed (back must be >= 0)")
        if i < 0:
            return float("nan")
        return float(self._a[i])

    def __getitem__(self, back: int) -> float:
        return self(back)

    def slice(self, n: int) -> np.ndarray:
        """The last n values up to and including the current bar."""
        lo = max(0, self._ctx.i - n + 1)
        return self._a[lo: self._ctx.i + 1]

    def rising(self, n: int = 1) -> bool:
        return self(0) > self(n) if not np.isnan(self(n)) else False

    def falling(self, n: int = 1) -> bool:
        return self(0) < self(n) if not np.isnan(self(n)) else False

    def crossed_above(self, other: "_Series | float") -> bool:
        o0 = other(0) if isinstance(other, _Series) else other
        o1 = other(1) if isinstance(other, _Series) else other
        return self(1) <= o1 and self(0) > o0

    def crossed_below(self, other: "_Series | float") -> bool:
        o0 = other(0) if isinstance(other, _Series) else other
        o1 = other(1) if isinstance(other, _Series) else other
        return self(1) >= o1 and self(0) < o0


class Context:
    """Everything a strategy is allowed to know at bar `i`."""

    def __init__(self, df: pd.DataFrame, ind: dict[str, np.ndarray],
                 instrument: Instrument):
        self.inst = instrument
        self.index = df.index
        self.i = 0
        self._raw = {
            "open": df["open"].to_numpy(), "high": df["high"].to_numpy(),
            "low": df["low"].to_numpy(), "close": df["close"].to_numpy(),
            "volume": df["volume"].to_numpy(),
        }
        self._ind = dict(ind)
        self._views: dict[str, _Series] = {}
        for k, arr in {**self._raw, **self._ind}.items():
            self._views[k] = _Series(np.asarray(arr, dtype="float64"), self)
        # Live run state, refreshed by the engine each bar.
        self.position = None
        self.equity = 0.0
        self.bars_since_entry = 0
        self.minutes_to_close = 1e9
        self.is_new_day = False
        self.bar_of_day = 0
        # Set by a strategy to ask the broker to protect an open trade. Applied
        # at this bar's close, so it can only affect subsequent bars.
        self.request_breakeven = False

    def __getattr__(self, name: str) -> _Series:
        views = self.__dict__.get("_views", {})
        if name in views:
            return views[name]
        raise AttributeError(f"no series named {name!r}; "
                             f"available: {sorted(views)}")

    def has(self, name: str) -> bool:
        return name in self._views

    @property
    def time(self) -> pd.Timestamp:
        return self.index[self.i]

    @property
    def in_position(self) -> bool:
        return self.position is not None

    @property
    def is_long(self) -> bool:
        return self.position is not None and self.position.side == "long"

    @property
    def is_short(self) -> bool:
        return self.position is not None and self.position.side == "short"

    def ready(self, *names: str) -> bool:
        """True when every named series has a real value at this bar."""
        for n in names:
            v = self._views[n](0)
            if v != v:
                return False
        return True


class Strategy:
    name = "unnamed"
    description = ""
    intraday = True            # square off at the session close
    long_only = False

    def __init__(self, **params):
        defaults = {p.name: p.default for p in self.params()}
        unknown = set(params) - set(defaults)
        if unknown:
            raise ValueError(f"{self.name}: unknown parameters {sorted(unknown)}")
        self.p = {**defaults, **{k: v for k, v in params.items() if v is not None}}

    # ---------------------------------------------------------------- to fill
    @classmethod
    def params(cls) -> list[Param]:
        return []

    def prepare(self, df: pd.DataFrame, inst: Instrument) -> dict[str, np.ndarray]:
        raise NotImplementedError

    def on_bar(self, ctx: Context) -> Signal | Exit | None:
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers
    def warmup(self) -> int:
        """Bars to skip before trading. Default: derived from int params, which
        is right for anything using lookback windows."""
        ints = [v for v in self.p.values() if isinstance(v, int) and not isinstance(v, bool)]
        return (max(ints) * 3) if ints else 50

    @classmethod
    def describe(cls) -> dict:
        import re, textwrap
        # Unwrap the source-code line breaks but keep paragraph breaks, so the
        # UI can reflow the text to whatever width it has.
        text = textwrap.dedent(cls.description).strip()
        paragraphs = re.split(r"\n\s*\n", text)
        text = "\n\n".join(" ".join(par.split()) for par in paragraphs)
        return {"name": cls.name, "description": text,
                "intraday": cls.intraday, "long_only": cls.long_only,
                "params": [p.to_dict() for p in cls.params()]}
