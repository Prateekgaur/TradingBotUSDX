"""Risk control -- the part of the system that actually protects the account.

A strategy decides *when* to trade. This decides *how much*, and *whether the
account is allowed to trade at all right now*. Most blown accounts are not the
result of a bad signal; they are the result of a good signal sized wrong, or
of trading on through a losing streak that should have stopped the day.

Every limit here is a hard gate checked before an entry is allowed:

  risk_per_trade_pct   size so the stop costs a fixed % of equity, no more
  max_position_pct     leverage cap, independent of the stop distance
  daily_loss_limit_pct stop trading for the rest of the day after a bad day
  daily_profit_lock_pct optional: stop after a great day, protect the win
  max_drawdown_pct     kill switch for the whole run
  max_trades_per_day   overtrading brake
  loss_streak_pause    cool off after N losses in a row
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0      # % of equity lost if the stop is hit
    max_position_pct: float = 100.0      # notional cap as % of equity
    daily_loss_limit_pct: float = 3.0    # 0 disables
    daily_profit_lock_pct: float = 0.0   # 0 disables
    max_drawdown_pct: float = 25.0       # 0 disables; halts the run
    max_trades_per_day: int = 0          # 0 disables
    loss_streak_pause: int = 0           # pause after N consecutive losses
    pause_bars: int = 10                 # how long that pause lasts
    min_stop_ticks: float = 2.0          # reject absurdly tight stops
    allow_short: bool = True
    fixed_qty: float = 0.0               # >0 overrides % sizing entirely


@dataclass
class RiskState:
    day: object = None
    day_start_equity: float = 0.0
    day_pnl: float = 0.0
    trades_today: int = 0
    loss_streak: int = 0
    pause_until_index: int = -1
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    blocks: dict[str, int] = field(default_factory=dict)


class RiskManager:
    def __init__(self, cfg: RiskConfig, starting_equity: float):
        self.cfg = cfg
        self.state = RiskState(day_start_equity=starting_equity, peak_equity=starting_equity)

    # ------------------------------------------------------------ day rollover
    def on_bar(self, ts: pd.Timestamp, equity: float) -> None:
        d = ts.date()
        if self.state.day != d:
            self.state.day = d
            self.state.day_start_equity = equity
            self.state.day_pnl = 0.0
            self.state.trades_today = 0
        self.state.peak_equity = max(self.state.peak_equity, equity)

        if self.cfg.max_drawdown_pct > 0 and self.state.peak_equity > 0:
            dd = (self.state.peak_equity - equity) / self.state.peak_equity * 100.0
            if dd >= self.cfg.max_drawdown_pct and not self.state.halted:
                self.state.halted = True
                self.state.halt_reason = (
                    f"max drawdown {dd:.1f}% >= limit {self.cfg.max_drawdown_pct:.1f}% "
                    f"on {ts:%Y-%m-%d}")

    def on_trade_closed(self, pnl: float, bar_index: int) -> None:
        self.state.day_pnl += pnl
        self.state.trades_today += 1
        if pnl < 0:
            self.state.loss_streak += 1
            if self.cfg.loss_streak_pause and self.state.loss_streak >= self.cfg.loss_streak_pause:
                self.state.pause_until_index = bar_index + self.cfg.pause_bars
                self.state.loss_streak = 0
        else:
            self.state.loss_streak = 0

    # -------------------------------------------------------------- permission
    def can_enter(self, bar_index: int, equity: float, side: str) -> tuple[bool, str]:
        s, c = self.state, self.cfg
        if s.halted:
            return False, "halted"
        if side == "short" and not c.allow_short:
            return False, "shorts_disabled"
        if bar_index < s.pause_until_index:
            return False, "loss_streak_pause"
        if c.max_trades_per_day and s.trades_today >= c.max_trades_per_day:
            return False, "max_trades_per_day"
        if c.daily_loss_limit_pct > 0 and s.day_start_equity > 0:
            lost = -s.day_pnl / s.day_start_equity * 100.0
            if lost >= c.daily_loss_limit_pct:
                return False, "daily_loss_limit"
        if c.daily_profit_lock_pct > 0 and s.day_start_equity > 0:
            gained = s.day_pnl / s.day_start_equity * 100.0
            if gained >= c.daily_profit_lock_pct:
                return False, "daily_profit_lock"
        return True, ""

    def note_block(self, reason: str) -> None:
        self.state.blocks[reason] = self.state.blocks.get(reason, 0) + 1

    # ------------------------------------------------------------------ sizing
    def size(self, equity: float, entry: float, stop: float, tick_size: float,
             point_value: float, qty_step: float, min_qty: float) -> float:
        """Risk-based position size.

        qty = (equity * risk%) / (stop distance * point value)

        Sized off the stop distance, not off a fixed lot count, so a wide-stop
        setup automatically takes a smaller position. This is what keeps one
        volatile day from costing what ten quiet days made.
        """
        c = self.cfg
        if c.fixed_qty > 0:
            return c.fixed_qty
        dist = abs(entry - stop)
        if dist < c.min_stop_ticks * tick_size:
            return 0.0
        risk_cash = equity * c.risk_per_trade_pct / 100.0
        qty = risk_cash / (dist * point_value)

        if c.max_position_pct > 0 and entry > 0:
            cap = (equity * c.max_position_pct / 100.0) / (entry * point_value)
            qty = min(qty, cap)

        steps = int(qty / qty_step) if qty_step > 0 else int(qty)
        qty = steps * qty_step
        return qty if qty >= min_qty else 0.0
