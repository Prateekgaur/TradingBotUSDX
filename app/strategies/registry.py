"""Strategy lookup. Add a class here and it appears in the UI, the optimiser
and the walk-forward runner with no other wiring."""
from __future__ import annotations

from app.strategies.base import Strategy
from app.strategies.intraday import (BollingerMeanReversion, OpeningRangeBreakout,
                                     VwapPullback)
from app.strategies.trend import DonchianBreakout, EmaTrendATR

ALL: list[type[Strategy]] = [
    EmaTrendATR,
    OpeningRangeBreakout,
    VwapPullback,
    BollingerMeanReversion,
    DonchianBreakout,
]

BY_NAME: dict[str, type[Strategy]] = {c.name: c for c in ALL}


def get(name: str) -> type[Strategy]:
    if name not in BY_NAME:
        raise KeyError(f"unknown strategy {name!r}; have {sorted(BY_NAME)}")
    return BY_NAME[name]


def build(name: str, params: dict | None = None) -> Strategy:
    cls = get(name)
    clean = {}
    for p in cls.params():
        if params and p.name in params and params[p.name] is not None:
            v = params[p.name]
            if p.kind == "int":
                v = int(float(v))
            elif p.kind == "float":
                v = float(v)
            elif p.kind == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            clean[p.name] = v
    return cls(**clean)


def describe_all() -> list[dict]:
    return [c.describe() for c in ALL]
