/* Trading Lab UI. Talks to the local FastAPI server; holds no trading logic
   of its own -- every number on screen comes from the engine. */

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—"
  : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v) => v === null || v === undefined ? "—" : `${Number(v) >= 0 ? "+" : ""}${fmt(v)}%`;
const cls = (v) => v > 0 ? "pos" : v < 0 ? "neg" : "neu";
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const state = { datasets: [], strategies: [], strat: null, last: null, charts: null, playing: null };

/* ------------------------------------------------------------------ plumbing */
async function api(path, body) {
  const res = await fetch(path, body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : undefined);
  const data = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}
function busy(msg) { const s = $("status"); s.className = "status busy"; s.textContent = msg; s.classList.remove("hidden"); }
function fail(e) { const s = $("status"); s.className = "status err"; s.textContent = String(e.message || e); s.classList.remove("hidden"); }
function clearStatus() { $("status").classList.add("hidden"); }
function tab(name) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach(p => p.classList.toggle("on", p.id === `tab-${name}`));
  if (name === "runs") loadRuns();
  if (state.charts) setTimeout(resizeCharts, 30);
}

/* ---------------------------------------------------------------- meta setup */
async function boot() {
  try {
    const [ds, st] = await Promise.all([api("/api/datasets"), api("/api/strategies")]);
    state.datasets = ds.datasets; state.strategies = st.strategies;
    fillSymbols();
    fillStrategies();
    if (!state.datasets.length) {
      $("dsHint").innerHTML = "No CSV data found. Seed some first:<br><code>python -m app.data.seed --symbols RELIANCE TCS --timeframe 15m</code>";
    }
  } catch (e) { fail(e); }
}

function fillSymbols() {
  const syms = [...new Set(state.datasets.map(d => d.symbol))].sort();
  $("symbol").innerHTML = syms.map(s => `<option>${esc(s)}</option>`).join("")
    || `<option value="">(no data)</option>`;
  fillTimeframes();
}

function fillTimeframes() {
  const sym = $("symbol").value;
  const order = ["1m", "2m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"];
  const tfs = state.datasets.filter(d => d.symbol === sym).map(d => d.timeframe)
    .sort((a, b) => order.indexOf(a) - order.indexOf(b));
  $("timeframe").innerHTML = tfs.map(t => `<option>${esc(t)}</option>`).join("");
  const preferred = tfs.includes("15m") ? "15m" : tfs[0];
  if (preferred) $("timeframe").value = preferred;
  updateHint();
}

function updateHint() {
  const d = state.datasets.find(x => x.symbol === $("symbol").value && x.timeframe === $("timeframe").value);
  $("dsHint").textContent = d ? `${d.bars.toLocaleString()} bars · ${d.file}` : "—";
}

function fillStrategies() {
  $("strategy").innerHTML = state.strategies.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");
  selectStrategy();
}

function selectStrategy() {
  state.strat = state.strategies.find(s => s.name === $("strategy").value);
  $("stratDesc").textContent = state.strat ? state.strat.description : "";
  renderParams();
}

function renderParams() {
  const host = $("params");
  if (!state.strat) return host.innerHTML = "";
  host.innerHTML = state.strat.params.map(p => {
    const id = `p_${p.name}`;
    if (p.kind === "bool") {
      return `<label class="check"><input type="checkbox" id="${id}" ${p.default ? "checked" : ""}> ${esc(p.name)}
        ${p.help ? `<span class="micro">${esc(p.help)}</span>` : ""}</label>`;
    }
    const step = p.step ?? (p.kind === "int" ? 1 : 0.1);
    return `<label>${esc(p.name)}
      <input type="number" id="${id}" value="${p.default}" step="${step}"
        ${p.low != null ? `min="${p.low}"` : ""} ${p.high != null ? `max="${p.high}"` : ""}>
      ${p.help ? `<span class="micro">${esc(p.help)}</span>` : ""}</label>`;
  }).join("");
}

function readParams() {
  const out = {};
  (state.strat?.params || []).forEach(p => {
    const el = $(`p_${p.name}`);
    if (!el) return;
    out[p.name] = p.kind === "bool" ? el.checked : Number(el.value);
  });
  return out;
}

function readRun() {
  return {
    symbol: $("symbol").value,
    timeframe: $("timeframe").value,
    strategy: $("strategy").value,
    params: readParams(),
    start: $("start").value || null,
    end: $("end").value || null,
    starting_equity: Number($("equity").value),
    pessimistic_intrabar: $("pessimistic_intrabar").checked,
    risk: {
      risk_per_trade_pct: Number($("risk_per_trade_pct").value),
      daily_loss_limit_pct: Number($("daily_loss_limit_pct").value),
      max_drawdown_pct: Number($("max_drawdown_pct").value),
      max_trades_per_day: Number($("max_trades_per_day").value),
      loss_streak_pause: Number($("loss_streak_pause").value),
      allow_short: $("allow_short").checked,
    },
  };
}

/* ------------------------------------------------------------------ charting */
function makeCharts() {
  const common = {
    layout: { background: { color: "#151a21" }, textColor: "#8b95a5", fontSize: 11 },
    grid: { vertLines: { color: "#1d232c" }, horzLines: { color: "#1d232c" } },
    rightPriceScale: { borderColor: "#272e38" },
    timeScale: { borderColor: "#272e38", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
  };
  const price = LightweightCharts.createChart($("chart"), { ...common, height: $("chart").clientHeight });
  const candles = price.addCandlestickSeries({
    upColor: "#2f9e6e", downColor: "#c2554d", borderVisible: false,
    wickUpColor: "#2f9e6e", wickDownColor: "#c2554d",
  });
  const eq = LightweightCharts.createChart($("equityChart"), { ...common, height: $("equityChart").clientHeight });
  const equity = eq.addLineSeries({ color: "#4d8df5", lineWidth: 2, priceLineVisible: false });
  const base = eq.addLineSeries({ color: "#3a4453", lineWidth: 1, lineStyle: 2, priceLineVisible: false });
  state.charts = { price, candles, eq, equity, base };
  window.addEventListener("resize", resizeCharts);
  // Keep the two time axes locked together.
  price.timeScale().subscribeVisibleLogicalRangeChange(r => r && eq.timeScale().setVisibleLogicalRange(r));
  eq.timeScale().subscribeVisibleLogicalRangeChange(r => r && price.timeScale().setVisibleLogicalRange(r));
}
function resizeCharts() {
  if (!state.charts) return;
  state.charts.price.applyOptions({ width: $("chart").clientWidth, height: $("chart").clientHeight });
  state.charts.eq.applyOptions({ width: $("equityChart").clientWidth, height: $("equityChart").clientHeight });
}

function drawRun(r, upto) {
  if (!state.charts) makeCharts();
  const c = state.charts;
  const candles = upto == null ? r.candles : r.candles.slice(0, upto + 1);
  const cutoff = candles.length ? candles[candles.length - 1].time : Infinity;
  c.candles.setData(candles);
  c.candles.setMarkers(r.markers.filter(m => m.time <= cutoff));
  c.equity.setData(r.equity.filter(p => p.time <= cutoff));
  c.base.setData((upto == null ? r.equity : r.equity.filter(p => p.time <= cutoff))
    .map(p => ({ time: p.time, value: r.stats ? r.startEquity : 0 })));
  // Size first: fitContent on a chart that has not been laid out yet computes
  // its bar spacing from the wrong width and lands zoomed into a few bars.
  resizeCharts();
  if (upto == null) {
    requestAnimationFrame(() => {
      resizeCharts();
      c.price.timeScale().fitContent();
      c.eq.timeScale().fitContent();
    });
  }
}

/* ------------------------------------------------------------------ backtest */
async function runBacktest() {
  const req = readRun();
  if (!req.symbol) return fail(new Error("No dataset selected. Seed data first."));
  busy(`Running ${req.strategy} on ${req.symbol} ${req.timeframe}…`);
  $("runBtn").disabled = true;
  try {
    const r = await api("/api/backtest", req);
    r.startEquity = req.starting_equity;
    state.last = r;
    clearStatus();
    drawRun(r, null);
    $("replaySlider").max = Math.max(0, r.candles.length - 1);
    $("replaySlider").value = r.candles.length - 1;
    $("replayLabel").textContent = "full run";
    renderKpis(r.stats, r);
    renderStats(r);
    renderTrades(r);
    tab("chart");
  } catch (e) { fail(e); } finally { $("runBtn").disabled = false; }
}

function renderKpis(s, r) {
  const box = (k, v, c = "neu") => `<div class="kpi"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`;
  $("kpiStrip").innerHTML =
    box("Net P&L", `${s.net_profit >= 0 ? "+" : ""}${fmt(s.net_profit, 0)}`, cls(s.net_profit)) +
    box("Return", pct(s.return_pct), cls(s.return_pct)) +
    box("Max drawdown", `-${fmt(s.max_drawdown_pct)}%`, s.max_drawdown_pct > 15 ? "neg" : "neu") +
    box("Trades", s.trades) +
    box("Win rate", `${fmt(s.win_rate_pct, 1)}%`) +
    box("Profit factor", s.profit_factor == null ? "∞" : fmt(s.profit_factor)) +
    box("Expectancy", `${fmt(s.expectancy_r, 2)}R`, cls(s.expectancy_r)) +
    box("Buy &amp; hold", pct(s.buy_hold_return_pct), "neu") +
    `<div class="kpi"><div class="k">Verdict</div><div class="v"><span class="grade ${s.grade}">${s.grade}</span></div></div>` +
    (r.halted ? box("HALTED", "kill switch", "neg") : "");
}

function renderStats(r) {
  const s = r.stats;
  const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
  const notes = (s.warnings || []).map(w =>
    `<div class="note ${w.severity}"><b>${w.severity} confidence risk</b>${esc(w.message)}</div>`).join("")
    || `<div class="note low"><b>checks</b>Nothing obviously wrong with this result — which is not the same as an edge.</div>`;

  const dataNotes = (r.data_warnings || []).map(w => `<div class="note medium"><b>data</b>${esc(w)}</div>`).join("");
  const blocks = Object.entries(r.blocks || {});
  const monthly = (s.monthly_returns || []).map(m =>
    `<tr><td>${m.month}</td><td class="${cls(m.return_pct)}">${pct(m.return_pct)}</td></tr>`).join("");
  const exits = Object.entries(s.exits || {}).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${v.count}</td><td class="${cls(v.pnl)}">${fmt(v.pnl, 0)}</td></tr>`).join("");

  $("tab-stats").innerHTML = `
    <div class="section-title">What to be suspicious of</div>
    <div class="notes">${notes}${dataNotes}
      ${r.halted ? `<div class="note high"><b>halted</b>${esc(r.halt_reason)}</div>` : ""}</div>

    <div class="section-title">Performance</div>
    <div class="cards">
      <div class="card"><h4>Returns</h4>
        ${kv("Net profit", fmt(s.net_profit, 0))}${kv("Return", pct(s.return_pct))}
        ${kv("CAGR", pct(s.cagr_pct))}${kv("Final equity", fmt(s.final_equity, 0))}
        ${kv("Buy &amp; hold", pct(s.buy_hold_return_pct))}${kv("Years tested", fmt(s.years, 2))}</div>
      <div class="card"><h4>Risk</h4>
        ${kv("Max drawdown", `-${fmt(s.max_drawdown_pct)}%`)}
        ${kv("Drawdown length", `${s.max_drawdown_bars} bars`)}
        ${kv("Buy &amp; hold drawdown", `-${fmt(s.buy_hold_max_dd_pct)}%`)}
        ${kv("Sharpe", fmt(s.sharpe))}${kv("Sortino", fmt(s.sortino))}${kv("Calmar", fmt(s.calmar))}</div>
      <div class="card"><h4>Trade quality</h4>
        ${kv("Trades", s.trades)}${kv("Win rate", `${fmt(s.win_rate_pct, 1)}%`)}
        ${kv("Profit factor", s.profit_factor == null ? "∞" : fmt(s.profit_factor))}
        ${kv("Expectancy", `${fmt(s.expectancy_r, 3)}R / ${fmt(s.expectancy_cash, 0)}`)}
        ${kv("Avg win / loss", `${fmt(s.avg_win, 0)} / ${fmt(s.avg_loss, 0)}`)}
        ${kv("Payoff ratio", fmt(s.payoff_ratio))}</div>
      <div class="card"><h4>Pain</h4>
        ${kv("Worst trade", fmt(s.worst_trade, 0))}${kv("Best trade", fmt(s.best_trade, 0))}
        ${kv("Longest losing run", s.max_consecutive_losses)}
        ${kv("Avg adverse excursion", `${fmt(s.avg_mae_r)}R`)}
        ${kv("Avg favourable excursion", `${fmt(s.avg_mfe_r)}R`)}
        ${kv("Exposure", `${fmt(s.exposure_pct, 1)}%`)}</div>
      <div class="card"><h4>Frictions</h4>
        ${kv("Gross profit", fmt(s.gross_profit, 0))}${kv("Total costs", fmt(s.total_costs, 0))}
        ${kv("Cost drag", `${fmt(s.cost_drag_pct, 1)}% of gross`)}
        ${kv("Slippage", `${r.instrument.slippage_ticks} ticks/side`)}
        ${kv("Commission", `${fmt(r.instrument.commission_pct * 100, 3)}%/side`)}
        ${kv("Avg bars held", fmt(s.avg_bars_held, 1))}</div>
      <div class="card"><h4>Run</h4>
        ${kv("Symbol", `${esc(r.symbol)} ${esc(r.timeframe)}`)}
        ${kv("Bars", r.bars.toLocaleString())}
        ${kv("From", esc((r.range || [""])[0]).slice(0, 16))}
        ${kv("To", esc((r.range || ["", ""])[1]).slice(0, 16))}
        ${kv("Engine time", `${r.elapsed_s}s`)}${kv("Saved as", esc(r.run_id || "—"))}</div>
    </div>

    ${blocks.length ? `<div class="section-title">Entries the risk rules refused</div>
      <div class="scroll"><table><thead><tr><th>Rule</th><th>Times blocked</th></tr></thead><tbody>
      ${blocks.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join("")}
      </tbody></table></div>` : ""}

    <div class="section-title">How trades ended</div>
    <div class="scroll"><table><thead><tr><th>Exit</th><th>Count</th><th>P&amp;L</th></tr></thead><tbody>${exits || `<tr><td colspan=3>—</td></tr>`}</tbody></table></div>

    <div class="section-title">Month by month</div>
    <div class="scroll" style="max-height:260px"><table><thead><tr><th>Month</th><th>Return</th></tr></thead><tbody>${monthly || `<tr><td colspan=2>—</td></tr>`}</tbody></table></div>`;
}

function renderTrades(r) {
  const t = r.trades || [];
  if (!t.length) { $("tab-trades").innerHTML = `<div class="empty">No trades were taken. Check the "Entries the risk rules refused" table under Results.</div>`; return; }
  $("tab-trades").innerHTML = `<div class="scroll"><table>
    <thead><tr><th>#</th><th>Side</th><th>Entry</th><th>Price</th><th>Exit</th><th>Price</th>
      <th>Qty</th><th>P&amp;L</th><th>R</th><th>Bars</th><th>MAE (R)</th><th>Reason</th></tr></thead>
    <tbody>${t.map((x, i) => `<tr>
      <td>${i + 1}</td><td>${x.side}</td>
      <td>${esc(x.entry_time).slice(0, 16)}</td><td>${fmt(x.entry_price)}</td>
      <td>${esc(x.exit_time).slice(0, 16)}</td><td>${fmt(x.exit_price)}</td>
      <td>${fmt(x.qty, 0)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 0)}</td>
      <td class="${cls(x.r_multiple)}">${fmt(x.r_multiple)}</td><td>${x.bars_held}</td>
      <td>${fmt(x.mae)}</td><td>${esc(x.exit_reason)}</td></tr>`).join("")}</tbody></table></div>`;
}

/* -------------------------------------------------------------------- replay */
function onReplay() {
  if (!state.last) return;
  const i = Number($("replaySlider").value);
  const last = state.last.candles.length - 1;
  drawRun(state.last, i);
  $("replayLabel").textContent = i >= last ? "full run"
    : new Date(state.last.candles[i].time * 1000).toISOString().replace("T", " ").slice(0, 16);
}
function togglePlay() {
  if (state.playing) { clearInterval(state.playing); state.playing = null; $("playBtn").textContent = "▶ Replay"; return; }
  if (!state.last) return;
  $("playBtn").textContent = "❚❚ Pause";
  const slider = $("replaySlider");
  if (Number(slider.value) >= Number(slider.max)) slider.value = Math.floor(Number(slider.max) * 0.2);
  state.playing = setInterval(() => {
    const next = Number(slider.value) + 1;
    if (next > Number(slider.max)) return togglePlay();
    slider.value = next; onReplay();
  }, 60);
}

/* ------------------------------------------------------------------ optimise */
function modal(title, bodyHtml, onOk) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = bodyHtml;
  $("modal").classList.remove("hidden");
  $("modalOk").onclick = async () => { $("modal").classList.add("hidden"); await onOk(); };
}
$("modalCancel").onclick = () => $("modal").classList.add("hidden");

function searchableParams() {
  return (state.strat?.params || []).filter(p => p.kind !== "bool" && p.low != null && p.high != null);
}

function paramChips() {
  return `<div class="chips">${searchableParams().map(p =>
    `<button type="button" class="chip on" data-p="${esc(p.name)}">${esc(p.name)}</button>`).join("")}</div>`;
}
function chosenParams() {
  return [...document.querySelectorAll(".chip.on")].map(c => c.dataset.p);
}
function wireChips() {
  document.querySelectorAll(".chip").forEach(c => c.onclick = () => c.classList.toggle("on"));
}

function openSweep() {
  modal("Optimise parameters", `
    <p class="micro">Searches parameter combinations on this data and ranks them. Everything it
    finds is in-sample: it is a shortlist to walk-forward test, never a result to trade.</p>
    <label>Objective <select id="objective">
      <option value="robust">robust — edge per unit of risk, penalised for drawdown</option>
      <option value="calmar">calmar — return over max drawdown</option>
      <option value="sharpe">sharpe</option>
      <option value="profit_factor">profit factor</option>
      <option value="expectancy">expectancy (R)</option>
      <option value="net_profit">net profit — the easiest to overfit</option>
    </select></label>
    <div class="row"><label>Max combinations <input type="number" id="maxEvals" value="250" min="10" max="3000"></label>
      <label>Min trades to qualify <input type="number" id="minTrades" value="20" min="1"></label></div>
    <label>Parameters to search${paramChips()}</label>`, async () => {
    const req = { ...readRun(), objective: $("objective").value, max_evals: Number($("maxEvals").value), min_trades: Number($("minTrades").value), only: chosenParams(), top: 30 };
    busy("Searching parameter space…");
    try { renderSweep(await api("/api/sweep", req)); clearStatus(); tab("sweep"); }
    catch (e) { fail(e); }
  });
  wireChips();
}

function renderSweep(r) {
  $("tab-sweep").innerHTML = `
    <div class="note medium"><b>read this first</b>${esc(r.caution)}</div>
    <div class="section-title">${r.viable} of ${r.tested} combinations produced a usable result · objective: ${esc(r.objective)}</div>
    <div class="scroll"><table><thead><tr>
      <th>#</th><th>Score</th><th>Neighbours</th><th>Return</th><th>Max DD</th><th>PF</th>
      <th>Exp (R)</th><th>Sharpe</th><th>Trades</th><th>Parameters</th><th></th></tr></thead>
    <tbody>${r.top.map((x, i) => `<tr>
      <td>${i + 1}</td><td>${fmt(x.score, 3)}</td>
      <td title="mean score of the nearest parameter sets — a lone peak is a curve fit">${fmt(x.neighbourhood, 3)}</td>
      <td class="${cls(x.return_pct)}">${pct(x.return_pct)}</td><td>-${fmt(x.max_drawdown_pct)}%</td>
      <td>${x.profit_factor == null ? "∞" : fmt(x.profit_factor)}</td><td>${fmt(x.expectancy_r, 3)}</td>
      <td>${fmt(x.sharpe)}</td><td>${x.trades}</td>
      <td style="text-align:left">${esc(JSON.stringify(x.params))}</td>
      <td><button class="chip" data-apply='${esc(JSON.stringify(x.params))}'>use</button></td>
      </tr>`).join("")}</tbody></table></div>`;
  document.querySelectorAll("[data-apply]").forEach(b => b.onclick = () => {
    const p = JSON.parse(b.dataset.apply);
    Object.entries(p).forEach(([k, v]) => { const el = $(`p_${k}`); if (el) { if (el.type === "checkbox") el.checked = !!v; else el.value = v; } });
    tab("chart"); runBacktest();
  });
}

function openWalkForward() {
  modal("Walk-forward test", `
    <p class="micro">Optimises on a training window, then trades the next window with those
    parameters frozen. Repeats across the history. The resulting equity curve was produced
    entirely out of sample — it is the closest thing here to an honest forecast.</p>
    <div class="row"><label>Folds <input type="number" id="folds" value="5" min="2" max="12"></label>
      <label>Train fraction <input type="number" id="trainFrac" value="0.7" step="0.05" min="0.5" max="0.9"></label></div>
    <div class="row"><label>Max combinations/fold <input type="number" id="maxEvals" value="150" min="10" max="1500"></label>
      <label>Min trades <input type="number" id="minTrades" value="10" min="1"></label></div>
    <label>Objective <select id="objective">
      <option value="robust">robust</option><option value="calmar">calmar</option>
      <option value="sharpe">sharpe</option><option value="expectancy">expectancy</option>
      <option value="profit_factor">profit factor</option><option value="net_profit">net profit</option>
    </select></label>
    <label>Parameters to search${paramChips()}</label>`, async () => {
    const req = {
      ...readRun(), folds: Number($("folds").value), train_frac: Number($("trainFrac").value),
      objective: $("objective").value, max_evals: Number($("maxEvals").value),
      min_trades: Number($("minTrades").value), only: chosenParams(),
    };
    busy("Walk-forward: optimising and re-testing each fold. This takes a while…");
    try { renderWalk(await api("/api/walkforward", req)); clearStatus(); tab("wf"); }
    catch (e) { fail(e); }
  });
  wireChips();
}

function renderWalk(r) {
  const s = r.oos_stats;
  const stab = Object.entries(r.param_stability || {}).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${esc(JSON.stringify(v.values))}</td><td>${fmt(v.cv, 2)}</td>
     <td class="${v.stable ? "pos" : "neg"}">${v.stable ? "stable" : "drifting"}</td></tr>`).join("");
  $("tab-wf").innerHTML = `
    <div class="kpi-strip">
      <div class="kpi"><div class="k">Verdict</div><div class="v"><span class="grade ${r.verdict}">${r.verdict}</span></div></div>
      <div class="kpi"><div class="k">Out-of-sample return</div><div class="v ${cls(s.return_pct)}">${pct(s.return_pct)}</div></div>
      <div class="kpi"><div class="k">OOS max drawdown</div><div class="v">-${fmt(s.max_drawdown_pct)}%</div></div>
      <div class="kpi"><div class="k">OOS trades</div><div class="v">${s.trades}</div></div>
      <div class="kpi"><div class="k">Profit factor</div><div class="v">${s.profit_factor == null ? "∞" : fmt(s.profit_factor)}</div></div>
      <div class="kpi"><div class="k">WF efficiency</div><div class="v">${fmt(r.efficiency, 2)}</div></div>
    </div>
    <div class="notes">${(r.notes || []).map(n => `<div class="note ${r.verdict === "reject" ? "high" : "medium"}">${esc(n)}</div>`).join("")}</div>
    <div class="section-title">Fold by fold — training result vs the unseen window that followed</div>
    <div class="scroll"><table><thead><tr>
      <th>Fold</th><th>Test window</th><th>In-sample</th><th>Out-of-sample</th><th>OOS DD</th>
      <th>OOS PF</th><th>OOS trades</th><th>Chosen parameters</th></tr></thead>
    <tbody>${r.folds.map(f => `<tr>
      <td>${f.index}</td><td style="text-align:left">${esc(f.test[0]).slice(0, 10)} → ${esc(f.test[1]).slice(0, 10)}</td>
      <td class="${cls(f.train_return_pct)}">${pct(f.train_return_pct)}</td>
      <td class="${cls(f.test_return_pct)}">${pct(f.test_return_pct)}</td>
      <td>-${fmt(f.test_max_dd_pct)}%</td><td>${f.test_profit_factor == null ? "∞" : fmt(f.test_profit_factor)}</td>
      <td>${f.test_trades}</td><td style="text-align:left">${esc(JSON.stringify(f.params))}</td></tr>`).join("")}</tbody></table></div>
    <div class="section-title">Did the winning parameters hold still?</div>
    <div class="scroll"><table><thead><tr><th>Parameter</th><th>Per fold</th><th>Variation</th><th></th></tr></thead>
      <tbody>${stab || `<tr><td colspan=4>—</td></tr>`}</tbody></table></div>`;
}

/* ---------------------------------------------------------------------- runs */
async function loadRuns() {
  try {
    const { runs } = await api("/api/runs");
    $("tab-runs").innerHTML = runs.length ? `<div class="scroll"><table><thead><tr>
      <th>When</th><th>Kind</th><th>Symbol</th><th>Strategy</th><th>Return</th><th>Max DD</th>
      <th>Trades</th><th>Verdict</th></tr></thead><tbody>
      ${runs.map(r => `<tr><td style="text-align:left">${esc(r.created_at).replace("T", " ").slice(0, 16)}</td>
        <td>${esc(r.kind)}</td><td>${esc(r.symbol)} ${esc(r.timeframe)}</td><td>${esc(r.strategy)}</td>
        <td class="${cls(r.return_pct)}">${pct(r.return_pct)}</td><td>${r.max_drawdown_pct == null ? "—" : "-" + fmt(r.max_drawdown_pct) + "%"}</td>
        <td>${r.trades ?? "—"}</td><td>${r.grade || r.verdict || "—"}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No saved runs yet.</div>`;
  } catch (e) { fail(e); }
}

/* --------------------------------------------------------------------- wiring */
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => tab(b.dataset.tab));
$("symbol").onchange = fillTimeframes;
$("timeframe").onchange = updateHint;
$("strategy").onchange = selectStrategy;
$("runBtn").onclick = runBacktest;
$("sweepBtn").onclick = openSweep;
$("wfBtn").onclick = openWalkForward;
$("replaySlider").oninput = onReplay;
$("playBtn").onclick = togglePlay;
boot();
