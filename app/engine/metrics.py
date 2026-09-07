"""Performance statistics, plus an honesty pass.

Return numbers alone are the easiest thing in this codebase to fool yourself
with. `reality_check` exists to argue with a good-looking equity curve: too
few trades, profit that came from one lucky outlier, an edge that vanishes
after costs, a drawdown no human would actually sit through.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.engine.broker import Trade

TRADING_DAYS = 252


def _periods_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return TRADING_DAYS
    med = pd.Series(index).diff().median()
    if pd.isna(med) or med <= pd.Timedelta(0):
        return TRADING_DAYS
    if med >= pd.Timedelta(days=1):
        return TRADING_DAYS / max(1.0, med / pd.Timedelta(days=1))
    bars_per_day = pd.Timedelta(hours=6.25) / med      # NSE cash session
    return TRADING_DAYS * float(bars_per_day)


def drawdown(equity: pd.Series) -> tuple[pd.Series, float, int, pd.Timestamp | None]:
    peak = equity.cummax()
    dd = (equity - peak) / peak.replace(0, np.nan)
    max_dd = float(dd.min() * 100.0) if len(dd) else 0.0
    trough = dd.idxmin() if len(dd) and not pd.isna(dd.min()) else None
    # Longest stretch spent below a previous peak, in bars.
    under = (equity < peak).to_numpy()
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return dd * 100.0, abs(max_dd), longest, trough


def compute(trades: list[Trade], equity: pd.Series, price: pd.DataFrame,
            starting_equity: float) -> dict:
    n = len(trades)
    pnls = np.array([t.pnl for t in trades], dtype="float64") if n else np.zeros(0)
    rs = np.array([t.r_multiple for t in trades], dtype="float64") if n else np.zeros(0)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]

    final = float(equity.iloc[-1]) if len(equity) else starting_equity
    net = final - starting_equity
    ret_pct = net / starting_equity * 100.0 if starting_equity else 0.0

    dd_series, max_dd, dd_bars, dd_at = drawdown(equity)
    ppy = _periods_per_year(equity.index)
    years = len(equity) / ppy if ppy else 0.0
    cagr = ((final / starting_equity) ** (1 / years) - 1) * 100.0 \
        if years > 0.05 and final > 0 and starting_equity > 0 else 0.0

    rets = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    sd = float(rets.std(ddof=0))
    sharpe = float(rets.mean() / sd * math.sqrt(ppy)) if sd > 0 else 0.0
    downside = rets[rets < 0]
    dsd = float(downside.std(ddof=0)) if len(downside) > 1 else 0.0
    sortino = float(rets.mean() / dsd * math.sqrt(ppy)) if dsd > 0 else 0.0

    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    costs = float(sum(t.costs for t in trades))
    gross_pnl = float(sum(t.gross_pnl for t in trades))

    streak = worst_streak = 0
    for p in pnls:
        streak = streak + 1 if p < 0 else 0
        worst_streak = max(worst_streak, streak)

    bars_in_market = int(sum(t.bars_held for t in trades))
    by_reason: dict[str, dict] = {}
    for t in trades:
        b = by_reason.setdefault(t.exit_reason, {"count": 0, "pnl": 0.0})
        b["count"] += 1
        b["pnl"] += t.pnl

    bh_start = float(price["close"].iloc[0])
    bh_end = float(price["close"].iloc[-1])
    buy_hold_pct = (bh_end / bh_start - 1) * 100.0 if bh_start else 0.0
    bh_eq = starting_equity * price["close"] / bh_start
    _, bh_dd, _, _ = drawdown(bh_eq)

    stats = {
        "trades": n,
        "net_profit": round(net, 2),
        "return_pct": round(ret_pct, 2),
        "final_equity": round(final, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "max_drawdown_bars": dd_bars,
        "max_drawdown_at": str(dd_at) if dd_at is not None else None,
        "calmar": round(cagr / max_dd, 2) if max_dd > 0.01 else 0.0,
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "win_rate_pct": round(len(wins) / n * 100.0, 2) if n else 0.0,
        "profit_factor": round(pf, 2) if pf != math.inf else None,
        "expectancy_r": round(float(rs.mean()), 3) if n else 0.0,
        "expectancy_cash": round(float(pnls.mean()), 2) if n else 0.0,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "payoff_ratio": round(abs(wins.mean() / losses.mean()), 2)
                        if len(wins) and len(losses) and losses.mean() else 0.0,
        "best_trade": round(float(pnls.max()), 2) if n else 0.0,
        "worst_trade": round(float(pnls.min()), 2) if n else 0.0,
        "max_consecutive_losses": worst_streak,
        "avg_bars_held": round(bars_in_market / n, 1) if n else 0.0,
        "exposure_pct": round(bars_in_market / len(equity) * 100.0, 1) if len(equity) else 0.0,
        "total_costs": round(costs, 2),
        "gross_profit": round(gross_pnl, 2),
        "cost_drag_pct": round(costs / abs(gross_pnl) * 100.0, 1) if gross_pnl else 0.0,
        "buy_hold_return_pct": round(buy_hold_pct, 2),
        "buy_hold_max_dd_pct": round(bh_dd, 2),
        "years": round(years, 2),
        "avg_mae_r": round(float(np.mean([t.mae for t in trades])), 2) if n else 0.0,
        "avg_mfe_r": round(float(np.mean([t.mfe for t in trades])), 2) if n else 0.0,
        "exits": {k: {"count": v["count"], "pnl": round(v["pnl"], 2)}
                  for k, v in sorted(by_reason.items())},
    }
    stats["monthly_returns"] = monthly_returns(equity)
    stats["warnings"] = reality_check(stats, trades)
    stats["grade"] = grade(stats)
    return stats


def monthly_returns(equity: pd.Series) -> list[dict]:
    if len(equity) < 2:
        return []
    m = equity.resample("ME").last().dropna()
    if len(m) < 1:
        return []
    first = pd.Series([equity.iloc[0]], index=[m.index[0] - pd.Timedelta(days=1)])
    m = pd.concat([first, m])
    r = m.pct_change().dropna() * 100.0
    return [{"month": f"{ts:%Y-%m}", "return_pct": round(float(v), 2)}
            for ts, v in r.items()]


def reality_check(s: dict, trades: list[Trade]) -> list[dict]:
    """Reasons not to trust this result. Severity: `high` means do not put
    money behind this number until it is resolved."""
    out: list[dict] = []

    def add(sev, msg):
        out.append({"severity": sev, "message": msg})

    n = s["trades"]
    if n == 0:
        add("high", "No trades were taken. Loosen the entry filters, widen the "
                    "date range, or check the risk limits are not blocking every entry.")
        return out
    if n < 30:
        add("high", f"Only {n} trades. Below ~30 the statistics are noise, not evidence. "
                    f"Test more history or a faster timeframe before believing this.")
    elif n < 100:
        add("medium", f"{n} trades is a thin sample; treat the win rate as +/- 10%.")

    if s["profit_factor"] is not None and 0 < s["profit_factor"] < 1.2 and s["net_profit"] > 0:
        add("medium", f"Profit factor {s['profit_factor']} is barely above break-even. "
                      f"A small increase in real slippage would erase it.")

    if s["cost_drag_pct"] > 40:
        add("high", f"Costs ate {s['cost_drag_pct']}% of gross profit. This is a "
                    f"commission-generation strategy, not a trading strategy.")
    elif s["cost_drag_pct"] > 20:
        add("medium", f"Costs are {s['cost_drag_pct']}% of gross profit -- fragile to any "
                      f"worsening in fills.")

    if n:
        best_share = abs(s["best_trade"]) / abs(s["net_profit"]) * 100 if s["net_profit"] else 0
        if s["net_profit"] > 0 and best_share > 30:
            add("high", f"One trade produced {best_share:.0f}% of the net profit. Remove it "
                        f"and the edge may not exist.")

    if s["max_drawdown_pct"] > 25:
        add("high", f"Max drawdown {s['max_drawdown_pct']}%. Ask honestly whether you would "
                    f"keep trading this system after that loss -- most people do not, which "
                    f"means the backtest return is unreachable in practice.")
    elif s["max_drawdown_pct"] > 15:
        add("medium", f"Max drawdown {s['max_drawdown_pct']}% with "
                      f"{s['max_consecutive_losses']} losses in a row at worst.")

    if s["net_profit"] > 0 and s["return_pct"] < s["buy_hold_return_pct"] \
            and s["max_drawdown_pct"] >= s["buy_hold_max_dd_pct"]:
        add("high", f"Buy and hold returned {s['buy_hold_return_pct']}% versus this system's "
                    f"{s['return_pct']}%, at no worse a drawdown. The strategy is destroying value.")

    if s["expectancy_r"] <= 0 and s["net_profit"] > 0:
        add("medium", "Positive cash profit but non-positive expectancy per unit of risk -- "
                      "the result depends on position sizing, not on the signal.")

    if s["exposure_pct"] > 90:
        add("low", "In the market over 90% of the time; this is closer to holding than trading.")

    holds = [t.bars_held for t in trades]
    if holds and np.median(holds) <= 1:
        add("medium", "Median hold is one bar. Results at this speed are dominated by "
                      "fill assumptions, which a bar-level backtest cannot model well.")
    return out


def grade(s: dict) -> str:
    """Blunt single-word summary. Deliberately hard to earn."""
    if s["trades"] < 30 or s["net_profit"] <= 0:
        return "unproven"
    pf = s["profit_factor"] or 0
    if any(w["severity"] == "high" for w in s["warnings"]):
        return "suspect"
    if pf >= 1.6 and s["max_drawdown_pct"] < 15 and s["expectancy_r"] > 0.15 and s["sharpe"] > 1:
        return "promising"
    if pf >= 1.25 and s["max_drawdown_pct"] < 25:
        return "marginal"
    return "weak"
