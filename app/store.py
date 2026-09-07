"""Saved runs, so a result you liked in March can be reproduced in June.

Every run stores its full configuration alongside its stats. Comparing two
strategies is only meaningful when you can prove they saw the same data with
the same costs, and memory is not proof.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.settings import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- backtest | sweep | walkforward
    symbol      TEXT, timeframe TEXT, strategy TEXT,
    label       TEXT,
    params      TEXT, config TEXT,
    stats       TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def save(kind: str, symbol: str, timeframe: str, strategy: str, params: dict,
         config: dict, stats: dict, payload: dict, label: str = "") -> str:
    rid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "INSERT INTO runs (id, created_at, kind, symbol, timeframe, strategy, "
            "label, params, config, stats, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, datetime.now(timezone.utc).isoformat(timespec="seconds"), kind,
             symbol, timeframe, strategy, label, json.dumps(params),
             json.dumps(config, default=str), json.dumps(stats, default=str),
             json.dumps(payload, default=str)))
    return rid


def list_runs(limit: int = 60) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, created_at, kind, symbol, timeframe, strategy, label, stats "
            "FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        st = json.loads(r["stats"] or "{}")
        out.append({
            "id": r["id"], "created_at": r["created_at"], "kind": r["kind"],
            "symbol": r["symbol"], "timeframe": r["timeframe"],
            "strategy": r["strategy"], "label": r["label"],
            "return_pct": st.get("return_pct"), "trades": st.get("trades"),
            "max_drawdown_pct": st.get("max_drawdown_pct"),
            "profit_factor": st.get("profit_factor"), "grade": st.get("grade"),
            "verdict": st.get("verdict"),
        })
    return out


def get(run_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "created_at": r["created_at"], "kind": r["kind"],
            "symbol": r["symbol"], "timeframe": r["timeframe"],
            "strategy": r["strategy"], "label": r["label"],
            "params": json.loads(r["params"] or "{}"),
            "config": json.loads(r["config"] or "{}"),
            "stats": json.loads(r["stats"] or "{}"),
            "payload": json.loads(r["payload"] or "{}")}


def delete(run_id: str) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM runs WHERE id = ?", (run_id,)).rowcount > 0
