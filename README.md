# Trading Lab

**A practice trading app that runs on your own computer.**

You pick a stock, pick a trading strategy, and press a button. The app replays
real market history one candle at a time and shows you what would have happened
if that strategy had been trading with your money — every entry, every exit,
every rupee of profit or loss, including brokerage.

Nothing here is connected to a broker. **No real order can ever be placed.** It is
a flight simulator, not a plane.

---

# Part 1 — Getting it running

You do not need to know how to program. You need to type a few things once.

## Step 1: Install Python

Python is the language the app is written in. Your computer probably does not
have it yet.

1. Go to **https://www.python.org/downloads/**
2. Click the big yellow **Download Python** button.
3. Run the file you downloaded.
4. **IMPORTANT:** on the first screen, tick the box that says
   **"Add Python to PATH"** (bottom of the window) *before* clicking Install.
   If you miss this, nothing else will work.
5. Click **Install Now**, wait, then **Close**.

### Check it worked

Press the **Windows key**, type `cmd`, press **Enter**. A black window opens.
Type this and press Enter:

```bash
python --version
```

You should see something like `Python 3.12.4`. Any version **3.11 or higher** is
fine. If it says *"python is not recognized"*, Python was installed without the
PATH box ticked — uninstall it and redo Step 1.

## Step 2: Start the app

Open the `D:\Trading` folder in File Explorer. Double-click:

```
run.bat
```

**The first time only**, a black window will sit there for 2–5 minutes saying it
is installing things. That is normal — leave it alone. It is downloading the
libraries the app needs.

When it finishes, your web browser opens automatically at:

```
http://127.0.0.1:8000
```

That address means *"this computer"*. The app is not on the internet. Nobody else
can see it.

**Leave the black window open while you use the app.** Closing it switches the app
off. When you are done, click on it and press `Ctrl + C`, or just close it.

To use the app again another day: double-click `run.bat` again. It will be fast
this time.

### If something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| "Setup failed. Is Python 3.11+ installed?" | Python missing or not on PATH | Redo Step 1, tick "Add Python to PATH" |
| Black window flashes and vanishes | Same as above | Redo Step 1 |
| "Errno 10048" / "address already in use" | The app is already running | Look for another black window — close it and try again |
| Browser says "can't reach this page" | The app is still starting | Wait 10 seconds, refresh the page |

---

# Part 2 — Getting market data

The app cannot invent prices. It needs a history file for each stock you want to
test. You have three options.

## Option A — Use the data that is already here (easiest)

The `data` folder already contains history for 9 Indian stocks and indices —
Reliance, TCS, HDFC Bank, Infosys, ICICI Bank, SBI, Axis Bank, Nifty 50 and
Bank Nifty. Nothing to do. Skip to Part 3.

> If you downloaded this project from GitHub, the `data` folder will be **empty** —
> price files are too large to store online. Use Option B below to fill it.

## Option B — Download fresh data automatically

Double-click:

```
seed.bat
```

It downloads about 10 years of daily history and around 2 months of intraday
history from Yahoo Finance, for the list of stocks written inside that file.
Takes a few minutes. You need internet for this step, and only this step.

**To change which stocks it downloads:** right-click `seed.bat` → *Edit*, and
change the names on the `set SYMS=` line. The names available are listed in
`app/settings.py`. Save, then run it again.

**Two things to know about free Yahoo data:**

- Intraday (5-minute, 15-minute) history is **capped at about 60 days**. That is
  not enough history to prove a day-trading strategy really works. Daily data
  goes back 10 years, which is plenty.
- It is not perfectly clean. Occasional bars are missing. The app warns you when
  it spots gaps, but it cannot invent the missing prices.

## Option C — Use your own data (best, and needed for serious work)

This is how you get past the 60-day limit, and how you test a stock or a market
that is not in the built-in list — including gold, forex or crypto.

**Where to get it:** your broker's website usually has an export button.
MetaTrader 5 has one built in: **Tools → History Centre → pick the symbol and
timeframe → Export**. Zerodha, Upstox, Angel One and most others can export CSV
too, and so can sites like investing.com.

**What to do with the file:**

1. Rename it to `SYMBOL_TIMEFRAME.csv` — for example `RELIANCE_5m.csv`,
   `NIFTY50_1d.csv`, `XAUUSD_15m.csv`.
   Allowed timeframes: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `1d`.
2. Drop it into the `D:\Trading\data` folder.
3. Refresh the app in your browser. The symbol appears in the dropdown.

**What the file has to contain.** One row per candle, with a date/time and the
four prices. The app is flexible about column names — it understands MetaTrader's
export format and most broker formats without you editing anything. A plain
example:

```
datetime,open,high,low,close,volume
2024-01-01 09:15,2450.00,2455.75,2448.10,2452.30,145200
2024-01-01 09:20,2452.30,2458.00,2451.00,2457.65,132900
```

It also accepts `Date` + `Time` in two separate columns, `Tick volume` instead of
`volume`, and semicolon or tab separators.

**If the app rejects your file**, the error message says exactly what is wrong.
The usual causes are: fewer than 50 rows, or a `high` that is lower than a `low`
somewhere (a corrupt export).

**Testing a market that is not in the list at all?** Open `app/settings.py` and
copy one of the existing `Instrument(...)` blocks, changing the symbol, currency,
tick size, brokerage and trading hours. That file is the only place a new market
needs to be added.

---

# Part 3 — Using the app

Across the top of the page are six tabs. Here is what each one is for and the
order to use them in.

## The Chart tab — your first backtest

1. **Symbol** — which stock. (e.g. `NIFTY50`)
2. **Timeframe** — how long each candle covers. `1d` = one candle per day.
   `15m` = one candle per 15 minutes.
3. **Strategy** — the set of rules that decides when to buy and sell. Start with
   `donchian_breakout` on a `1d` timeframe; it is the simplest to understand.
4. **Starting capital** — how much pretend money to begin with.
5. Press **Run backtest**.

Green and red arrows appear on the chart where the strategy bought and sold. The
wiggly line underneath is your **equity curve** — your account balance over time.
Up and to the right is good.

**Replay** steps through the test one candle at a time, so you can watch the
strategy trade instead of only seeing the summary. This is genuinely worth doing
once: it turns a set of numbers into something you can argue with.

## The Results tab — read this before believing anything

The numbers, and — more importantly — **a list of reasons not to trust them**.

The list at the bottom is the most valuable thing in the whole app. It says
things like *"Only 12 trades — too few to mean anything"* or *"Costs ate 207% of
gross profit"* or *"Buy and hold beat this strategy"*. It is the app arguing
against its own results.

The plain-English version of the main numbers:

| Number | What it actually means |
|---|---|
| **Return %** | How much the account grew or shrank |
| **Max drawdown %** | The worst peak-to-bottom fall along the way. If this is 30%, ask yourself honestly whether you would have kept trading after losing 30% of your money. Most people would not. |
| **Win rate %** | How often it won. **A high win rate is not the same as making money** — nine ₹100 wins and one ₹2,000 loss is a 90% win rate and a losing system. |
| **Profit factor** | Rupees won for every rupee lost. Below 1.0 loses money. Above 1.5 is decent. Above 3 on a small number of trades is usually a mirage. |
| **Expectancy (R)** | Average profit per trade, measured in units of what you risked. 0.2R means each trade averages a fifth of what you were risking. Positive is what matters. |
| **Trades** | How many. Under 30, treat everything above as noise. |
| **Grade** | The app's own one-word summary: *unproven, suspect, weak, marginal, promising*. Nothing here will ever be graded "excellent" — that grade does not exist, on purpose. |

## The Trades tab

Every single trade, with the profit, the R-multiple, and **MAE** — how far the
trade went against you before it worked out. MAE is the column that tells you
whether you could have psychologically survived this strategy.

## The Optimiser tab

Tries hundreds of variations of the strategy's settings and ranks them.

**This is the most dangerous screen in the app**, and it is deliberately labelled
that way. If you try 500 settings on the same history, some will look brilliant
purely by luck — the same way that if 500 people flip ten coins, someone gets ten
heads and looks like a genius. Optimiser results are a **shortlist to investigate**,
never an answer. Take the winner to the next tab.

## The Walk-forward tab — the only test that counts

This is the honest one, and the reason this app exists.

It splits your history into chunks. It optimises the strategy on the first chunk,
then **freezes those settings and trades the next chunk, which the optimiser never
saw**. Then it slides forward and repeats. It stitches all those unseen chunks
together into one equity curve.

That curve is the closest thing to an honest answer you can get without risking
money. At the end you get one of three verdicts:

- **SURVIVES** — worth taking to the next stage. Not "this makes money".
- **INCONCLUSIVE** — not enough evidence either way. Usually means too little data.
- **REJECT** — it worked on the past because it memorised the past. Bin it.

**A real example from this app:** the Donchian breakout on Nifty daily returns
between +30% and +62% in optimisation. Walk-forward: about **−5%**. Verdict:
**REJECT**. That result cost nothing to find out. Finding it out with real money
costs exactly what you think it costs.

## The History tab

Every run you have ever done, saved with its complete settings. A result from
March can be reproduced exactly in June. Use it to keep track of what you have
already ruled out.

---

# Part 4 — How to actually use this to become profitable

This is the part most trading software leaves out, so read it carefully. **The app
does not make you money. The process does.**

## The honest truth first

No trading system always makes profit and avoids loss. Anyone — any person, any
software, any YouTube video, any Telegram group — who tells you otherwise is
either mistaken or selling something. **Losses are not a bug in trading; they are
the cost of doing it.**

What separates profitable traders from everyone else is not the ability to avoid
losses. It is:

1. **Losing small and winning bigger.** A system that wins 40% of the time makes
   money comfortably if the wins are twice the size of the losses.
2. **Never letting one trade, or one day, do serious damage.**
3. **Only trading a system that was tested on data it had never seen.**

This app is built entirely around those three things.

## The process, in order

**Stage 1 — Get enough data.** Daily data is fine to start with, and you have 10
years of it. Do not try to prove an intraday strategy on 60 days of Yahoo data;
it cannot be done. Get broker exports first (Part 2, Option C).

**Stage 2 — Backtest with the defaults.** Run a strategy. Read the Results
warnings. Expect it to lose — **every strategy in this app loses money on the
default settings, and the app says so plainly**. That is the starting line, not a
failure.

**Stage 3 — Check it across many stocks.** Open a black command window in
`D:\Trading` and run:

```bash
.venv\Scripts\python -m app.cli scan --timeframe 1d --strategy donchian_breakout
```

This tests one strategy on every stock you have data for. **An edge that works on
one stock and nothing else is not an edge, it is a coincidence.** If it works on
6 out of 9, that is interesting.

**Stage 4 — Optimise, but expect to be fooled.** Use the Optimiser to shortlist a
few settings. Prefer settings where the *neighbouring* values also did well — a
lone spike in the results means you found luck, not an edge.

**Stage 5 — Walk-forward it.** Non-negotiable. If the verdict is REJECT, throw the
strategy away and go back to Stage 2 with a different one. **Most ideas die here.
That is the system working correctly.**

**Stage 6 — Forward paper test.** Even a SURVIVES verdict is not permission to
trade money. Write down the exact settings, then run the strategy forward on data
that did not exist when you chose those settings — a few weeks or months of new
bars, re-downloaded, and tested unchanged. This is the only test that cannot be
cheated, because the future genuinely had not happened yet.

**Stage 7 — Only then, real money, small.** Start with the smallest position your
broker allows. Your real results will be *worse* than the backtest — real slippage
is worse, and you will not follow the rules perfectly. If it survives three months
of small real trades, scale up slowly.

Realistically, expect Stages 2 to 5 to be repeated many times before something
survives. **That is not you failing. That is what the search actually looks like.**

## The settings that protect your account

Most blown accounts are not caused by a bad signal. They are caused by a good
signal sized wrong, or by a bad day that should have been stopped after the third
loss. These controls are in the sidebar, and they matter more than the strategy:

| Control | What it does | Sensible starting value |
|---|---|---|
| **Risk per trade %** | The single most important setting. Position size is calculated so that if the stop-loss is hit, you lose exactly this much of your account. A wider stop automatically means a smaller position. | **1%**, or 0.5% while learning |
| **Daily loss limit %** | Stops trading for the rest of the day once the day is down this much. Ends revenge trading before it starts. | **3%** |
| **Daily profit lock %** | Optionally stops after a very good day, so you keep it. | 4–6% |
| **Max drawdown %** | Kill switch. Halts the entire run. | **25%** |
| **Max trades/day** | Overtrading brake. | 3–5 |
| **Pause after N losses** | Cools off after a losing streak. | 3 |
| **Intraday square-off** | Intraday strategies close everything before the market shuts, so you never carry overnight gap risk. | Leave on |

Entries that these rules *refused* are counted and shown in Results, so you can
see what the protection cost you as well as what it saved.

**Practical advice:** never raise "risk per trade" above 2%. At 2% risk, a run of
ten losses — which is completely normal and will happen — costs you a fifth of
your account. At 5% risk, the same normal losing streak costs you 40% and ends
your trading.

## Warning signs that a good-looking result is fake

- **Fewer than 30 trades.** Not evidence. Luck.
- **Most of the profit came from one or two trades.** Remove them and look again.
- **It only works on one stock, or one year.**
- **A drawdown you would not have sat through.** A backtest never panics. You will.
- **Trades that last one or two candles.** Bar-by-bar simulation cannot model
  these honestly, and the app flags it when it sees it.
- **It looks amazing straight after optimisation.** Of course it does. That is
  what optimisation is for. Walk-forward it.

---

# Part 5 — For the technically curious

Everything below is optional reading.

## Why the results can be trusted

**No lookahead, structurally.** The commonest way backtests lie is by peeking at
prices that had not happened yet. Here, a strategy sees the market through an
object that *physically cannot* reach a future bar — asking for one raises an
error. A decision made as a candle closes fills at the **next** candle's open,
never the same one. There is a test that rewrites the last third of the price
history and asserts every earlier trade comes out bit-for-bit identical.

**Costs that hurt.** Every fill pays slippage in the direction that hurts, plus
brokerage, on both entry and exit. Defaults are set to a typical Indian discount
broker; change them per market in `app/settings.py`.

**Pessimistic fills.** If price gaps straight through your stop-loss, you fill at
the open — not at your stop price, which is what would really happen. If one
candle contains both your stop and your target, the app assumes the **stop** hit
first.

**A reality check on every run.** Too few trades, one lucky outlier carrying the
profit, an edge that dies after costs, a drawdown no human would tolerate,
buy-and-hold beating the system — each is flagged with a severity.

## The strategies included

| Name | Style | Idea |
|---|---|---|
| `ema_trend_atr` | intraday trend | Moving-average cross, filtered by a slower trend average and a trend-strength floor; volatility-based stop, break-even move, trailing stop |
| `opening_range_breakout` | intraday breakout | Break of the first N candles' range, with a width filter and a cut-off time |
| `vwap_pullback` | intraday | Trade the day's direction, entering on a pullback to the average price instead of chasing |
| `bollinger_mean_reversion` | intraday fade | Fade the extremes of a volatility band — only when the market is not trending |
| `donchian_breakout` | daily swing | Classic channel breakout with a volatility stop and channel exit |

Defaults are deliberately unoptimised.

## Writing your own strategy

Add a class to `app/strategies/`, register it in `registry.py`, and it appears in
the UI, the optimiser and the walk-forward runner with no further wiring.

```python
class MyStrategy(Strategy):
    name = "my_strategy"
    intraday = True

    @classmethod
    def params(cls):
        return [Param("lookback", 20, "int", 5, 60, 5)]

    def prepare(self, df, inst):
        # vectorised, computed once. Include "atr" to enable ATR stops/trailing.
        return {"atr": ta.atr(df["high"], df["low"], df["close"], 14),
                "ema": ta.ema(df["close"], self.p["lookback"])}

    def on_bar(self, ctx):
        if ctx.in_position:
            return None
        if ctx.close.crossed_above(ctx.ema):
            return Signal("long", stop_atr_mult=2.0, target_r=2.5)
```

## Command line

```bash
.venv\Scripts\python -m app.cli backtest --symbol NIFTY50 --timeframe 1d --strategy donchian_breakout
.venv\Scripts\python -m app.cli sweep    --symbol RELIANCE --timeframe 15m --strategy vwap_pullback
.venv\Scripts\python -m app.cli walk     --symbol NIFTY50 --timeframe 1d --strategy donchian_breakout --folds 5
.venv\Scripts\python -m app.cli scan     --timeframe 15m --strategy opening_range_breakout
```

## Tests

```bash
.venv\Scripts\python -m pytest tests -q
```

The broker tests are hand-computed — if the profit-and-loss arithmetic ever
drifts, they fail with a number you can check on paper.

## Layout

```
app/
  settings.py          markets: tick size, costs, trading hours
  data/loader.py       CSV -> validated candles; the engine never touches the network
  data/seed.py         downloads history into data/*.csv
  engine/indicators.py indicator maths
  engine/broker.py     fills, slippage, brokerage, stops, gaps
  engine/risk.py       position sizing and the limits that stop trading
  engine/backtest.py   the candle loop (do not reorder its steps)
  engine/metrics.py    statistics plus the reality check
  strategies/          the strategy API and the built-in strategies
  optimize/sweep.py    parameter search
  optimize/walkforward.py   out-of-sample validation
  api.py  cli.py  store.py  static/
data/                  your price files (not stored on GitHub)
tests/                 the test suite
```

## Known limits

- **Candle-level simulation.** Within one candle the app only knows the open,
  high, low and close, so it has to guess the order prices were touched in.
  Very fast strategies are not honestly testable this way, and the app says so.
- **No order book.** Slippage is a fixed number of ticks. Real slippage is worse
  in fast markets and in illiquid stocks.
- **One instrument at a time.** No portfolio-level sizing or correlation between
  positions. Use `cli scan` to check an edge across many symbols.
- **A backtest is not a forecast.** Surviving walk-forward means a strategy was
  not obviously fitted to the past. It is a licence to paper trade, not to fund
  an account.

---

## Safety

This app binds to `127.0.0.1` only — it is reachable from your computer and
nowhere else. It connects to no broker and holds no credentials. The only time it
touches the internet is when you run `seed.bat` to download price history. It is
structurally incapable of placing a real order.

**Nothing in this repository is financial advice.** Trading loses money for most
people who attempt it. Risk only what you can afford to lose entirely.
