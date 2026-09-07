"""Paper broker: fills, costs, stops, and the bookkeeping behind them.

Fill rules, stated plainly because these are where backtests lie:

  1. An order placed while bar i is closing fills at the OPEN of bar i+1,
     never at bar i's close. You cannot trade a price you have already seen.
  2. Market fills pay slippage: `slippage_ticks` against you, plus half the
     spread on each side for quoted instruments.
  3. If price GAPS through your stop, you fill at the open, not at the stop.
     This is the single biggest source of fake backtest equity.
  4. If a bar's range contains both the stop and the target, the STOP is
     assumed to hit first. Pessimistic, and correct often enough that the
     alternative flatters every mean-reversion system ever written.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

import pandas as pd

from app.settings import Instrument

Side = Literal["long", "short"]


@dataclass
class Order:
    side: Side
    qty: float
    reason: str = "signal"
    stop_loss: float | None = None
    take_profit: float | None = None
    trail_atr_mult: float | None = None
    tag: str = ""


@dataclass
class Position:
    side: Side
    qty: float
    entry_price: float
    entry_time: pd.Timestamp
    entry_index: int
    stop_loss: float | None
    take_profit: float | None
    initial_stop: float | None
    entry_cost: float
    trail_atr_mult: float | None = None
    high_water: float = 0.0
    low_water: float = 0.0
    tag: str = ""
    bars_held: int = 0
    partial_done: bool = False

    @property
    def dir(self) -> int:
        return 1 if self.side == "long" else -1

    def unrealised(self, price: float, point_value: float) -> float:
        return (price - self.entry_price) * self.dir * self.qty * point_value

    def risk_per_unit(self) -> float:
        if self.initial_stop is None:
            return 0.0
        return abs(self.entry_price - self.initial_stop)


@dataclass
class Trade:
    side: Side
    qty: float
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    gross_pnl: float
    costs: float
    pnl: float
    r_multiple: float
    bars_held: int
    exit_reason: str
    mae: float = 0.0          # worst excursion while open, in R
    mfe: float = 0.0          # best excursion while open, in R
    tag: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = str(self.entry_time)
        d["exit_time"] = str(self.exit_time)
        return d


class PaperBroker:
    def __init__(self, instrument: Instrument, starting_equity: float,
                 allow_short: bool = True, pessimistic_intrabar: bool = True):
        self.inst = instrument
        self.starting_equity = float(starting_equity)
        self.cash = float(starting_equity)
        self.allow_short = allow_short
        self.pessimistic = pessimistic_intrabar
        self.position: Position | None = None
        self.trades: list[Trade] = []
        self._slip = instrument.slippage_ticks * instrument.tick_size
        self._half_spread = instrument.spread_ticks * instrument.tick_size / 2.0

    # ---------------------------------------------------------------- pricing
    def _fill_price(self, side_dir: int, ref_price: float, entering: bool) -> float:
        """Slippage always moves price against the trader: you buy higher and
        sell lower, whether opening or closing."""
        adverse = side_dir if entering else -side_dir
        return self.inst.round_price(ref_price + adverse * (self._slip + self._half_spread))

    def _cost(self, price: float, qty: float) -> float:
        return self.inst.commission(price, qty)

    @property
    def equity(self) -> float:
        return self.cash

    def mark_to_market(self, price: float) -> float:
        eq = self.cash
        if self.position:
            eq += self.position.unrealised(price, self.inst.point_value)
        return eq

    # ----------------------------------------------------------------- orders
    def open_position(self, order: Order, bar_open: float, ts: pd.Timestamp,
                      index: int) -> Position | None:
        if self.position is not None or order.qty <= 0:
            return None
        if order.side == "short" and not self.allow_short:
            return None
        qty = self.inst.round_qty(order.qty)
        if qty < self.inst.min_qty:
            return None

        d = 1 if order.side == "long" else -1
        price = self._fill_price(d, bar_open, entering=True)
        cost = self._cost(price, qty)
        self.cash -= cost
        self.position = Position(
            side=order.side, qty=qty, entry_price=price, entry_time=ts,
            entry_index=index, stop_loss=order.stop_loss, take_profit=order.take_profit,
            initial_stop=order.stop_loss, entry_cost=cost,
            trail_atr_mult=order.trail_atr_mult, high_water=price, low_water=price,
            tag=order.tag)
        return self.position

    def close_position(self, ref_price: float, ts: pd.Timestamp, reason: str,
                       exact: bool = False) -> Trade | None:
        """`exact=True` fills at ref_price with no extra slippage: the order was
        a resting stop or limit that the market traded through, and any adverse
        move is already expressed by the gap rules in `process_bar`."""
        p = self.position
        if p is None:
            return None
        price = self.inst.round_price(ref_price) if exact else \
            self._fill_price(p.dir, ref_price, entering=False)
        gross = (price - p.entry_price) * p.dir * p.qty * self.inst.point_value
        cost = self._cost(price, p.qty)
        self.cash += gross - cost
        risk_cash = p.risk_per_unit() * p.qty * self.inst.point_value
        net = gross - cost - p.entry_cost
        rpu = p.risk_per_unit()
        trade = Trade(
            side=p.side, qty=p.qty, entry_time=p.entry_time, entry_price=p.entry_price,
            exit_time=ts, exit_price=price, gross_pnl=gross,
            costs=cost + p.entry_cost, pnl=net,
            r_multiple=(net / risk_cash) if risk_cash > 0 else 0.0,
            bars_held=p.bars_held, exit_reason=reason,
            mae=(((p.low_water - p.entry_price) * p.dir) / rpu) if rpu else 0.0,
            mfe=(((p.high_water - p.entry_price) * p.dir) / rpu) if rpu else 0.0,
            tag=p.tag)
        self.trades.append(trade)
        self.position = None
        return trade

    # ------------------------------------------------------------------ bars
    def process_bar(self, o: float, h: float, l: float, c: float,
                    ts: pd.Timestamp) -> Trade | None:
        """Walk one bar with a position open; returns a Trade if it exited.
        The order of these checks is deliberately unkind to the trader."""
        p = self.position
        if p is None:
            return None
        p.bars_held += 1
        if p.side == "long":
            p.high_water = max(p.high_water, h)
            p.low_water = min(p.low_water, l)
        else:
            p.high_water = min(p.high_water, l)   # "best" price for a short is lower
            p.low_water = max(p.low_water, h)

        sl, tp = p.stop_loss, p.take_profit
        if p.side == "long":
            if sl is not None and o <= sl:        # gapped through the stop
                return self.close_position(o, ts, "stop_gap", exact=True)
            if tp is not None and o >= tp:
                return self.close_position(o, ts, "target_gap", exact=True)
            hit_sl = sl is not None and l <= sl
            hit_tp = tp is not None and h >= tp
        else:
            if sl is not None and o >= sl:
                return self.close_position(o, ts, "stop_gap", exact=True)
            if tp is not None and o <= tp:
                return self.close_position(o, ts, "target_gap", exact=True)
            hit_sl = sl is not None and h >= sl
            hit_tp = tp is not None and l <= tp

        if hit_sl and hit_tp:
            take_stop = self.pessimistic
            return self.close_position(sl if take_stop else tp, ts,
                                       "stop_ambiguous" if take_stop else "target_ambiguous",
                                       exact=True)
        if hit_sl:
            return self.close_position(sl, ts, "stop", exact=True)
        if hit_tp:
            return self.close_position(tp, ts, "target", exact=True)
        return None

    def update_trailing(self, close: float, atr_value: float) -> None:
        """Trail on the CLOSE of a bar already tested for exits. Trailing from
        the bar's own extreme and then testing that same bar for a stop hit
        would be lookahead."""
        p = self.position
        if p is None or not p.trail_atr_mult:
            return
        if atr_value is None or atr_value != atr_value or atr_value <= 0:
            return
        dist = p.trail_atr_mult * atr_value
        if p.side == "long":
            new = self.inst.round_price(close - dist)
            if p.stop_loss is None or new > p.stop_loss:
                p.stop_loss = new
        else:
            new = self.inst.round_price(close + dist)
            if p.stop_loss is None or new < p.stop_loss:
                p.stop_loss = new

    def move_stop_to_breakeven(self, buffer_ticks: float = 1.0) -> None:
        p = self.position
        if p is None:
            return
        buf = buffer_ticks * self.inst.tick_size
        be = p.entry_price + p.dir * buf
        if p.side == "long" and (p.stop_loss is None or be > p.stop_loss):
            p.stop_loss = self.inst.round_price(be)
        elif p.side == "short" and (p.stop_loss is None or be < p.stop_loss):
            p.stop_loss = self.inst.round_price(be)
