/* Flower Overview - a clean, minimal list of all your stocks. */
"use strict";

const { api, num, price, pct, frac, big, money, cls, esc, ago, safeUrl, markdown, stream, debounce, isNum } = Flower;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const LWC = window.LightweightCharts;

const state = { rows: [], summary: null, filter: "all", sort: "value", q: "", selected: null, period: "1y" };
let heroChart = null, heroSeries = null, sheetChart = null, aiAbort = null;

/* ---------- theme ---------- */

function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
try { applyTheme(localStorage.getItem("flower-theme")); } catch (_) { /* storage blocked */ }
$("#theme").addEventListener("click", () => {
  const dark = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() === "#000";
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("flower-theme", next); } catch (_) { /* ignore */ }
  api("/api/settings", { method: "PUT", json: { theme: next } }).catch(() => {});
  restyleCharts();
});

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function chartOpts() {
  return {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: css("--muted"), fontFamily: css("--sans"), attributionLogo: false },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    rightPriceScale: { visible: false },
    leftPriceScale: { visible: false },
    timeScale: { visible: false, borderVisible: false },
    crosshair: { horzLine: { visible: false, labelVisible: false }, vertLine: { labelVisible: true, color: css("--muted"), style: 2 } },
    handleScroll: false, handleScale: false,
  };
}

function seriesColor(values) {
  const up = values.length < 2 || values[values.length - 1] >= values[0];
  return up ? css("--up") : css("--down");
}

function restyleCharts() {
  [heroChart, sheetChart].forEach((c) => c && c.applyOptions(chartOpts()));
  loadHero();
  if (state.selected) openSheet(state.selected);
}

/* ---------- avatar ---------- */

function avatar(sym, name) {
  let h = 0;
  for (const ch of sym) h = (h * 31 + ch.charCodeAt(0)) % 360;
  const letters = (name || sym).replace(/[^A-Za-z0-9 ]/g, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || sym[0];
  return `<span class="avatar" style="background:hsl(${h} 45% 38%)">${esc(letters)}</span>`;
}

function range(low, high, p) {
  if (![low, high, p].every(isNum) || high <= low) return `<span class="muted">–</span>`;
  const x = Math.max(0, Math.min(100, ((p - low) / (high - low)) * 100));
  return `<div class="range" title="52W ${price(low)} – ${price(high)}"><i style="left:${x}%"></i></div>`;
}

/* ---------- data ---------- */

async function loadOverview() {
  try {
    const d = await api("/api/overview");
    state.rows = d.rows;
    state.summary = d.summary;
    renderSummary();
    renderList();
  } catch (e) {
    $("#list").innerHTML = `<div class="empty err">Could not load data: ${esc(e.message)}</div>`;
  }
}

function renderSummary() {
  const s = state.summary;
  const cur = s.base_currency;
  if (!s.total_value) {
    $("#pv").textContent = money(0, cur);
    $("#pd").innerHTML = `<span class="muted">Add your holdings with the + button to see your portfolio.</span>`;
    return;
  }
  $("#pv").textContent = money(s.total_value, cur);
}

async function loadHero() {
  const el = $("#pchart");
  if (!heroChart) {
    heroChart = LWC.createChart(el, chartOpts());
    heroSeries = heroChart.addAreaSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  }
  let pts = [];
  try { pts = await api(`/api/portfolio/history?period=${state.period}`); } catch (_) { pts = []; }
  const values = pts.map((p) => p.value);
  const color = seriesColor(values);
  heroSeries.applyOptions({ lineColor: color, topColor: color + "33", bottomColor: color + "00" });
  heroSeries.setData(pts.map((p) => ({ time: p.time, value: p.value })));
  heroChart.timeScale().fitContent();
  const s = state.summary;
  if (!s || !s.total_value || !values.length) return;
  const cur = s.base_currency;
  const labels = { "1d": "today", "5d": "past week", "1mo": "past month", "6mo": "past 6 months", "1y": "past year", max: "since first buy" };
  if (state.period === "1d") {
    $("#pd").innerHTML = `<span class="${cls(s.day_change)}">${s.day_change >= 0 ? "▲" : "▼"} ${money(Math.abs(s.day_change), cur)} (${pct(s.day_change_pct)})</span> <span class="muted">today</span>`;
  } else {
    // Market gain only: change in value minus money added through new buys in the period.
    const first = pts[0], last = pts[pts.length - 1];
    const added = last.cost - first.cost;
    const diff = last.value - first.value - added;
    const rel = first.value + added ? (diff / (first.value + added)) * 100 : null;
    $("#pd").innerHTML = `<span class="${cls(diff)}">${diff >= 0 ? "▲" : "▼"} ${money(Math.abs(diff), cur)} (${pct(rel)})</span> <span class="muted">${labels[state.period]}</span>
      <span class="muted" style="margin-left:14px">Total return <span class="${cls(s.total_pl)}">${money(s.total_pl, cur)} (${pct(s.total_pl_pct)})</span></span>`;
  }
}

/* ---------- list ---------- */

function visibleRows() {
  const q = state.q.toLowerCase();
  const rows = state.rows.filter((r) =>
    (state.filter === "all" || (state.filter === "portfolio" ? r.in_portfolio : r.in_watchlist)) &&
    (!q || r.symbol.toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q)));
  const key = {
    value: (r) => -(r.value ?? -1), day: (r) => -(r.change_pct ?? -1e9), pl: (r) => -(r.pl_pct ?? -1e9), pe: (r) => r.pe ?? 1e9,
    div: (r) => -(r.div_yield ?? -1), mcap: (r) => -(r.market_cap ?? -1), score: (r) => -(r.score ?? -1), name: (r) => (r.name || r.symbol).toLowerCase(),
  }[state.sort];
  return rows.sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
}

function renderList() {
  const rows = visibleRows();
  const cur = state.summary ? state.summary.base_currency : "EUR";
  if (!state.rows.length) {
    $("#list").innerHTML = `<div class="empty">No stocks yet. Tap <b>+</b> to add holdings or stocks to watch.</div>`;
    return;
  }
  if (!rows.length) { $("#list").innerHTML = `<div class="empty">Nothing matches.</div>`; return; }
  $("#list").innerHTML = rows.map((r) => {
    const sub = r.in_portfolio ? `${num(r.shares, r.shares % 1 ? 3 : 0)} shares · ${esc(r.symbol)}` : `${esc(r.symbol)}${r.target_price ? ` · target ${price(r.target_price)}` : ""}`;
    const hit = r.target_price && r.price && r.price <= r.target_price;
    const pos = r.in_portfolio ? `<div>${money(r.value, cur)}</div><div class="small ${cls(r.pl)}">${pct(r.pl_pct)}</div>` : `<span class="badge ${hit ? "hit" : ""}">${hit ? "Target hit" : "Watching"}</span>`;
    return `<div class="row-item ${state.selected === r.symbol ? "sel" : ""}" data-s="${esc(r.symbol)}">
      <span class="who">${avatar(r.symbol, r.name)}<div><div class="n">${esc(r.name || r.symbol)}</div><div class="s">${sub}</div></div></span>
      <span class="c-pe">${num(r.pe, 1)}</span>
      <span class="c-div">${frac(r.div_yield)}</span>
      <span class="c-mcap">${big(r.market_cap)}</span>
      <span class="c-range">${range(r.low52, r.high52, r.price)}</span>
      <span class="c-sig"><span class="sig ${r.light || ""}" title="Signal score ${r.score ?? "–"}/100"></span></span>
      <span class="c-pos">${pos}</span>
      <span class="pricecell"><div class="price">${price(r.price)} <span class="small muted">${esc(r.currency || "")}</span></div><div class="small ${cls(r.change_pct)}">${pct(r.change_pct)}</div></span>
      <span class="meta"><span>P/E ${num(r.pe, 1)}</span><span>Div ${frac(r.div_yield)}</span><span>Cap ${big(r.market_cap)}</span>
        <span><span class="sig ${r.light || ""}"></span> ${r.score ?? "–"}</span>${r.in_portfolio ? `<span>${money(r.value, cur)} <span class="${cls(r.pl)}">${pct(r.pl_pct)}</span></span>` : ""}</span>
    </div>`;
  }).join("");
  $$(".row-item").forEach((el) => el.addEventListener("click", () => openSheet(el.dataset.s)));
}

$$("#filter button").forEach((b) => b.addEventListener("click", () => {
  state.filter = b.dataset.f;
  $$("#filter button").forEach((x) => x.classList.toggle("on", x === b));
  renderList();
}));
$("#q").addEventListener("input", debounce((e) => { state.q = e.target.value.trim(); renderList(); }, 120));
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; renderList(); });
$$("#periods button").forEach((b) => b.addEventListener("click", () => {
  state.period = b.dataset.p;
  $$("#periods button").forEach((x) => x.classList.toggle("on", x === b));
  loadHero();
}));

/* ---------- detail sheet ---------- */

function closeSheet() {
  state.selected = null;
  $("#sheet").hidden = true;
  $(".layout").classList.remove("with-sheet");
  if (sheetChart) { sheetChart.remove(); sheetChart = null; }
  if (aiAbort) { aiAbort.abort(); aiAbort = null; }
  $$(".row-item").forEach((x) => x.classList.remove("sel"));
  document.body.style.overflow = "";
}

async function openSheet(sym) {
  if (aiAbort) { aiAbort.abort(); aiAbort = null; }
  state.selected = sym;
  $$(".row-item").forEach((x) => x.classList.toggle("sel", x.dataset.s === sym));
  $("#sheet").hidden = false;
  $(".layout").classList.add("with-sheet");
  if (window.matchMedia("(max-width: 1100px)").matches) document.body.style.overflow = "hidden";
  const row = state.rows.find((r) => r.symbol === sym) || {};
  const body = $("#sheet-body");
  const cur = state.summary ? state.summary.base_currency : "EUR";
  body.innerHTML = `<button class="close" id="closeSheet" aria-label="Close">✕</button>
    <div class="sub">${esc(sym)}</div><h2>${esc(row.name || sym)}</h2>
    <div class="big" id="sPrice">${price(row.price)} <span class="muted" style="font-size:16px">${esc(row.currency || "")}</span></div>
    <div class="${cls(row.change_pct)}" style="font-weight:600">${pct(row.change_pct)} <span class="muted">today</span></div>
    <div class="chart" id="sChart"></div>
    <div class="chips" id="sPeriods">${["1d", "5d", "1mo", "6mo", "1y", "5y"].map((p) => `<button data-p="${p}" class="${p === "1y" ? "on" : ""}">${{ "1d": "1D", "5d": "1W", "1mo": "1M", "6mo": "6M", "1y": "1Y", "5y": "5Y" }[p]}</button>`).join("")}</div>
    ${row.in_portfolio ? `<h3>Your position</h3><div class="facts">
      <div class="fact"><div class="k">Value</div><div class="v">${money(row.value, cur)}</div></div>
      <div class="fact"><div class="k">Return</div><div class="v ${cls(row.pl)}">${money(row.pl, cur)} (${pct(row.pl_pct)})</div></div>
      <div class="fact"><div class="k">Shares</div><div class="v">${num(row.shares, row.shares % 1 ? 3 : 0)}</div></div></div>` : ""}
    <h3>Key figures</h3><div class="facts" id="sFacts"><div class="muted">Loading…</div></div>
    <h3>Signal check</h3><div class="checks" id="sSig"><div class="muted">Loading…</div></div>
    <h3>AI analysis</h3><div id="sAi"></div>
    <h3>News</h3><div class="news" id="sNews"><div class="muted">Loading…</div></div>
    <div class="btnrow"><a class="primary" href="/terminal#/sec/${encodeURIComponent(sym)}/des">Open in Terminal</a>
      ${row.in_watchlist ? `<button class="ghost" id="unwatch">Remove from watchlist</button>` : `<button class="ghost" id="watch">Add to watchlist</button>`}</div>`;
  $("#closeSheet").addEventListener("click", closeSheet);
  const w = $("#watch"), uw = $("#unwatch");
  if (w) w.addEventListener("click", async () => { await api("/api/watchlist", { method: "POST", json: { symbol: sym } }); await loadOverview(); openSheet(sym); });
  if (uw) uw.addEventListener("click", async () => { await api(`/api/watchlist/${encodeURIComponent(sym)}`, { method: "DELETE" }); await loadOverview(); openSheet(sym); });

  if (sheetChart) sheetChart.remove();
  const chart = LWC.createChart($("#sChart"), chartOpts());
  sheetChart = chart;
  const series = chart.addAreaSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  const loadChart = async (p) => {
    const rows = await api(`/api/history/${encodeURIComponent(sym)}?period=${p}`).catch(() => []);
    if (state.selected !== sym || sheetChart !== chart) return; // another stock was opened meanwhile
    const vals = rows.map((r) => r.close);
    const color = seriesColor(vals);
    series.applyOptions({ lineColor: color, topColor: color + "33", bottomColor: color + "00" });
    series.setData(rows.map((r) => ({ time: r.time, value: r.close })));
    sheetChart.timeScale().fitContent();
  };
  $$("#sPeriods button").forEach((b) => b.addEventListener("click", () => {
    $$("#sPeriods button").forEach((x) => x.classList.toggle("on", x === b));
    loadChart(b.dataset.p);
  }));
  loadChart("1y");

  api(`/api/quote/${encodeURIComponent(sym)}`).then((i) => {
    if (state.selected !== sym) return;
    const f = [["P/E", num(i.pe, 1)], ["Forward P/E", num(i.forward_pe, 1)], ["Dividend yield", frac(i.div_yield, 2)], ["Market cap", big(i.market_cap, i.currency)],
      ["Price / book", num(i.pb, 2)], ["Net margin", frac(i.net_margin)], ["Return on equity", frac(i.roe)], ["Debt / equity", num(i.debt_to_equity, 2)],
      ["Revenue growth", frac(i.revenue_growth)], ["Free cash flow", big(i.fcf)], ["52W high", price(i.high52)], ["52W low", price(i.low52)],
      ["Analyst target", price(i.target_mean)], ["Sector", esc(i.sector || "–")]];
    $("#sFacts").innerHTML = f.map(([k, v]) => `<div class="fact"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  }).catch(() => { $("#sFacts").innerHTML = `<div class="muted">Unavailable.</div>`; });

  api(`/api/signals/${encodeURIComponent(sym)}`).then((s) => {
    if (state.selected !== sym) return;
    $("#sSig").innerHTML = s.items.map((x) => `<div class="check"><span class="sig ${x.status}"></span>
      <div><div class="t">${esc(x.label)}</div><div class="note">${esc(x.note)}</div></div><div class="val">${esc(x.value)}</div></div>`).join("")
      + `<div class="note muted" style="font-size:12px">${esc(s.disclaimer)}</div>`;
  }).catch(() => { $("#sSig").innerHTML = `<div class="muted">Unavailable.</div>`; });

  api(`/api/news/${encodeURIComponent(sym)}`).then((n) => {
    if (state.selected !== sym) return;
    $("#sNews").innerHTML = n.length ? n.slice(0, 6).map((x) => `<a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener">${esc(x.title)}
      <div class="meta2">${esc(x.publisher || "")} · ${esc(ago(x.time))}</div></a>`).join("") : `<div class="muted">No news.</div>`;
  }).catch(() => { $("#sNews").innerHTML = `<div class="muted">Unavailable.</div>`; });

  renderAi(sym);
}

async function renderAi(sym) {
  const box = $("#sAi");
  const cached = await api(`/api/ai/report/${encodeURIComponent(sym)}?lens=general`).catch(() => null);
  if (state.selected !== sym) return;
  if (cached) {
    box.innerHTML = `<div class="ai">${markdown(cached.content, cached.sources)}</div><div class="muted small">Generated ${esc(cached.created)} · <a href="/terminal#/sec/${encodeURIComponent(sym)}/ai">details & chat</a></div>`;
    return;
  }
  box.innerHTML = `<p class="muted small">Let your AI model read the latest annual report and summarise growth, balance sheet, outlook and risks.</p>
    <button class="primary" id="genAi">Analyse annual report</button>`;
  $("#genAi").addEventListener("click", async () => {
    aiAbort = new AbortController();
    let text = "", sources = [];
    box.innerHTML = `<div class="muted small" id="aiSt">Starting…</div><div class="ai" id="aiOut"></div>`;
    try {
      await stream(`/api/ai/report/${encodeURIComponent(sym)}?lens=general`, null, (ev) => {
        if (state.selected !== sym) return;
        if (ev.type === "status") $("#aiSt").textContent = ev.text;
        if (ev.type === "sources") sources = ev.sources;
        if (ev.type === "delta") { text += ev.text; $("#aiOut").innerHTML = markdown(text, sources); }
        if (ev.type === "error") $("#aiSt").innerHTML = `<span class="err">${esc(ev.text)}</span>${ev.text.includes("Settings") ? ' <a href="/settings#ai">Open settings</a>' : ""}`;
        if (ev.type === "done") $("#aiSt").textContent = "AI output can contain errors. Check the cited sources.";
      }, aiAbort.signal);
    } catch (e) { if (e.name !== "AbortError") $("#aiSt").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  });
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.selected) closeSheet(); });

/* ---------- add dialog ---------- */

const dlg = $("#addDlg");
let picked = null, addType = "portfolio";

$("#add").addEventListener("click", () => {
  picked = null;
  $("#addForm").reset();
  $("#addResults").innerHTML = "";
  $("#addPicked").hidden = true;
  $("#addErr").textContent = "";
  dlg.showModal();
  $("#addSearch").focus();
});
$$("#addType button").forEach((b) => b.addEventListener("click", () => {
  addType = b.dataset.t;
  $$("#addType button").forEach((x) => x.classList.toggle("on", x === b));
  $("#pfFields").hidden = addType !== "portfolio";
  $("#wlFields").hidden = addType !== "watchlist";
}));
$("#addSearch").addEventListener("input", debounce(async (e) => {
  const q = e.target.value.trim();
  if (q.length < 2) { $("#addResults").innerHTML = ""; return; }
  const res = await api(`/api/search?q=${encodeURIComponent(q)}`).catch(() => []);
  $("#addResults").innerHTML = res.map((r, i) => `<button type="button" data-i="${i}"><b>${esc(r.symbol)}</b> ${esc(r.name)} <span class="muted small">${esc(r.exchange || "")}</span></button>`).join("")
    || `<div class="muted small">No match. You can also type the exact Yahoo symbol and press Enter.</div>`;
  $$("#addResults button").forEach((b) => b.addEventListener("click", () => pick(res[+b.dataset.i])));
}, 250));
$("#addSearch").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); const v = e.target.value.trim().toUpperCase(); if (v) pick({ symbol: v, name: v }); }
});

async function pick(r) {
  picked = r;
  $("#addResults").innerHTML = "";
  $("#addPicked").hidden = false;
  $("#addPicked").textContent = `${r.symbol} · ${r.name}`;
  const q = await api(`/api/quote/${encodeURIComponent(r.symbol)}`).catch(() => null);
  if (q && picked === r) {
    $("#curHint").textContent = q.currency ? `(${q.currency})` : "";
    const f = $("#addForm");
    if (!f.buy_price.value && q.price) f.buy_price.placeholder = price(q.price);
  }
}

$("#addForm").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;
  e.preventDefault();
  const f = e.target;
  $("#addErr").textContent = "";
  if (!picked) { $("#addErr").textContent = "Pick a stock first."; return; }
  try {
    if (addType === "portfolio") {
      if (!(+f.shares.value > 0) || f.buy_price.value === "") { $("#addErr").textContent = "Enter shares and buy price."; return; }
      await api("/api/portfolio", { method: "POST", json: { symbol: picked.symbol, shares: +f.shares.value, buy_price: +f.buy_price.value, buy_date: f.buy_date.value || null, note: f.note.value } });
    } else {
      await api("/api/watchlist", { method: "POST", json: { symbol: picked.symbol, target_price: f.target_price.value ? +f.target_price.value : null, note: f.note.value } });
    }
    dlg.close();
    await loadOverview();
    loadHero();
  } catch (err) { $("#addErr").textContent = err.message; }
});

/* ---------- boot ---------- */

api("/api/status").then((s) => {
  $("#demo").hidden = !s.demo;
  let stored = null;
  try { stored = localStorage.getItem("flower-theme"); } catch (_) { /* ignore */ }
  if (s.theme !== stored) {
    applyTheme(s.theme);
    try { localStorage.setItem("flower-theme", s.theme); } catch (_) { /* ignore */ }
    restyleCharts();
  }
  if (s.refresh_minutes > 0) {
    setInterval(() => { loadOverview().then(loadHero); }, s.refresh_minutes * 60_000);
  }
}).catch(() => {});
loadOverview().then(loadHero);
