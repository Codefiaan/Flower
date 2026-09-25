/* Flower Terminal - Bloomberg-style keyboard-first UI. Routes live in the URL hash. */
"use strict";

const { api, num, price, pct, frac, big, money, cls, esc, ago, safeUrl, markdown, stream, sparkline, debounce, isNum } = Flower;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const LWC = window.LightweightCharts;

const TABS = [
  ["des", "DES", "Overview"], ["gp", "GP", "Chart"], ["fa", "FA", "Financials"],
  ["ev", "EV", "Valuation"], ["cn", "CN", "News & events"], ["ai", "AI", "AI analysis"],
];
const VIEWS = { market: "MKT", portfolio: "PORT", watchlist: "WATCH", screener: "SCRN", news: "NEWS", help: "HELP" };
let charts = [];
let aiAbort = null;
let baseCurrency = "EUR";
let defaultLens = "general";

/* ---------- status & utilities ---------- */

function setStatus(text, kind = "") {
  const s = $("#status");
  s.textContent = text;
  s.className = kind;
}

async function load(label, fn) {
  setStatus(`LOADING ${label}...`, "busy");
  try {
    const r = await fn();
    setStatus("READY");
    return r;
  } catch (e) {
    setStatus(`ERROR ${label}: ${e.message}`, "err");
    throw e;
  }
}

function disposeCharts() {
  charts.forEach((c) => { try { c.remove(); } catch (_) { /* already gone */ } });
  charts = [];
  if (aiAbort) { aiAbort.abort(); aiAbort = null; }
}

function chartTheme(extra = {}) {
  return {
    autoSize: true,
    layout: { background: { type: "solid", color: "#0a0a0a" }, textColor: "#9a9a9a", fontFamily: "Consolas, monospace", fontSize: 11 },
    grid: { vertLines: { color: "#151515" }, horzLines: { color: "#151515" } },
    rightPriceScale: { borderColor: "#2a2a2a" },
    timeScale: { borderColor: "#2a2a2a", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
    ...extra,
  };
}

function rangeBar(low, high, p) {
  if (![low, high, p].every(isNum) || high <= low) return "–";
  const x = Math.max(0, Math.min(100, ((p - low) / (high - low)) * 100));
  return `<span class="range" title="52W ${price(low)} – ${price(high)}"><i style="left:calc(${x}% - 1px)"></i></span>`;
}

const dot = (light) => `<span class="dot ${light || ""}"></span>`;

/* ---------- routing ---------- */

function go(hash) {
  if (location.hash === hash) route(); else location.hash = hash;
}

function route() {
  disposeCharts();
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [a, b, c] = parts;
  $$("#fkeys button").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === (a || "market")));
  if (a === "sec" && b) {
    $("#crumb").textContent = `${decodeURIComponent(b)} ${(c || "des").toUpperCase()}`;
    return renderSecurity(decodeURIComponent(b).toUpperCase(), c || "des");
  }
  const v = VIEWS[a] ? a : "market";
  $("#crumb").textContent = VIEWS[v];
  ({ market: renderMarket, portfolio: renderPortfolio, watchlist: renderWatchlist, screener: renderScreener,
     news: renderNews, help: renderHelp })[v]();
}

window.addEventListener("hashchange", route);

/* ---------- command line ---------- */

const cmd = $("#cmd");
const sugg = $("#suggest");
let suggestions = [];
let active = -1;

function runCommand(text) {
  const t = text.trim().toUpperCase().replace(/<GO>/g, "").trim();
  if (!t) return;
  sugg.hidden = true;
  cmd.value = "";
  const words = t.split(/\s+/);
  const alias = { MKT: "market", MARKET: "market", PORT: "portfolio", PF: "portfolio", WATCH: "watchlist", WL: "watchlist",
                  SCRN: "screener", EQS: "screener", NEWS: "news", N: "news", HELP: "help", H: "help" };
  if (words.length === 1 && alias[words[0]]) return go(`#/${alias[words[0]]}`);
  if (words[0] === "SET" || words[0] === "SETTINGS") { location.href = "/settings"; return; }
  if (words[0] === "KEY" || words[0] === "APIKEY") { location.href = "/settings#ai"; return; }
  if (words[0] === "OV" || words[0] === "OVERVIEW") { location.href = "/overview"; return; }
  const sym = words[0];
  const fn = (words[1] || "DES").toLowerCase();
  const map = { des: "des", gp: "gp", fa: "fa", ev: "ev", val: "ev", cn: "cn", ai: "ai", go: "des" };
  go(`#/sec/${encodeURIComponent(sym)}/${map[fn] || "des"}`);
}

const doSuggest = debounce(async (q) => {
  if (q.length < 2 || q.includes(" ")) { sugg.hidden = true; return; }
  try {
    suggestions = await api(`/api/search?q=${encodeURIComponent(q)}`);
  } catch (_) { suggestions = []; }
  active = -1;
  if (!suggestions.length || cmd.value.trim() !== q) { sugg.hidden = true; return; }
  sugg.innerHTML = suggestions.map((s, i) =>
    `<div data-i="${i}"><span class="s">${esc(s.symbol)}</span><span>${esc(s.name)}</span><span class="x">${esc(s.exchange || "")} ${esc(s.type || "")}</span></div>`).join("");
  sugg.hidden = false;
}, 250);

cmd.addEventListener("input", () => doSuggest(cmd.value.trim()));
cmd.addEventListener("keydown", (e) => {
  if (!sugg.hidden && ["ArrowDown", "ArrowUp"].includes(e.key)) {
    e.preventDefault();
    active = (active + (e.key === "ArrowDown" ? 1 : -1) + suggestions.length) % suggestions.length;
    $$("div", sugg).forEach((d, i) => d.classList.toggle("active", i === active));
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    if (!sugg.hidden && active >= 0) runCommand(suggestions[active].symbol);
    else runCommand(cmd.value);
  }
  if (e.key === "Escape") { sugg.hidden = true; }
});
sugg.addEventListener("mousedown", (e) => {
  const d = e.target.closest("div[data-i]");
  if (d) { e.preventDefault(); runCommand(suggestions[+d.dataset.i].symbol); }
});
cmd.addEventListener("blur", () => setTimeout(() => { sugg.hidden = true; }, 150));
$("#go").addEventListener("click", () => runCommand(cmd.value));

document.addEventListener("keydown", (e) => {
  const keys = { F1: "market", F2: "portfolio", F3: "watchlist", F4: "screener", F5: "news", F9: "help" };
  if (keys[e.key]) { e.preventDefault(); go(`#/${keys[e.key]}`); return; }
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (!typing && (e.key === "/" || (e.key.length === 1 && /[a-z^]/i.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey))) {
    cmd.focus();
    if (e.key === "/") e.preventDefault();
  }
});
$$("#fkeys button").forEach((b) => b.addEventListener("click", () => go(`#/${b.dataset.view}`)));

setInterval(() => {
  $("#clock").textContent = new Date().toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}, 1000);

/* ---------- views: market ---------- */

async function renderMarket() {
  view.innerHTML = `
    <div class="panel"><h3>Markets <span class="right muted">1-month sparklines</span></h3><div class="body"><div class="tiles" id="tiles"><div class="empty">Loading...</div></div></div></div>
    <div class="grid g2" style="margin-top:8px">
      <div class="panel"><h3>Portfolio <span class="right"><button class="btn small" onclick="go('#/portfolio')">F2 open</button></span></h3><div class="body" id="mk-port"><div class="empty">Loading...</div></div></div>
      <div class="panel"><h3>Top news</h3><div class="body scroll" style="max-height:340px" id="mk-news"><div class="empty">Loading...</div></div></div>
    </div>
    <div class="panel" style="margin-top:8px"><h3>Watchlist <span class="right"><button class="btn small" onclick="go('#/watchlist')">F3 open</button></span></h3><div class="scroll" id="mk-watch"><div class="empty">Loading...</div></div></div>`;
  load("MARKET", () => api("/api/market")).then((tiles) => {
    $("#tiles").innerHTML = tiles.map((t) => `
      <div class="tile" data-s="${esc(t.symbol)}"><div class="n">${esc(t.name)} <span class="muted">${esc(t.symbol)}</span></div>
      <div class="p">${price(t.price)} <span class="${cls(t.change_pct)}" style="font-size:13px">${pct(t.change_pct)}</span></div>
      <canvas></canvas></div>`).join("");
    $$(".tile").forEach((el, i) => {
      sparkline($("canvas", el), tiles[i].spark);
      el.addEventListener("click", () => go(`#/sec/${encodeURIComponent(el.dataset.s)}/gp`));
    });
  }).catch(() => { $("#tiles").innerHTML = `<div class="empty err">Market data unavailable.</div>`; });

  api("/api/portfolio/summary").then((s) => {
    if (!s.positions.length) { $("#mk-port").innerHTML = `<div class="empty">No positions yet. Press F2 to add your holdings.</div>`; return; }
    const cur = s.base_currency;
    $("#mk-port").innerHTML = `
      <div class="kv" style="margin-bottom:8px">
        <div><span>Value</span><span>${money(s.total_value, cur)}</span></div>
        <div><span>Today</span><span class="${cls(s.day_change)}">${money(s.day_change, cur)} (${pct(s.day_change_pct)})</span></div>
        <div><span>Cost</span><span>${money(s.total_cost, cur)}</span></div>
        <div><span>Total P/L</span><span class="${cls(s.total_pl)}">${money(s.total_pl, cur)} (${pct(s.total_pl_pct)})</span></div>
      </div>
      <table class="t"><thead><tr><th>Symbol</th><th>Weight</th><th>Day</th><th>P/L %</th></tr></thead><tbody>
      ${s.positions.slice(0, 8).map((p) => `<tr class="click" data-s="${esc(p.symbol)}"><td class="sym">${esc(p.symbol)}</td><td>${pct(p.weight, 1, false)}</td>
        <td class="${cls(p.change_pct)}">${pct(p.change_pct)}</td><td class="${cls(p.pl_pct)}">${pct(p.pl_pct)}</td></tr>`).join("")}
      </tbody></table>`;
    bindRowClicks($("#mk-port"));
  }).catch((e) => { $("#mk-port").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; });

  api("/api/news").then((items) => { $("#mk-news").innerHTML = newsHtml(items.slice(0, 25)); })
    .catch((e) => { $("#mk-news").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; });

  api("/api/watchlist/summary").then((rows) => {
    $("#mk-watch").innerHTML = rows.length ? watchTable(rows, false) : `<div class="empty">Watchlist is empty. Press F3 to add stocks.</div>`;
    bindRowClicks($("#mk-watch"));
  }).catch((e) => { $("#mk-watch").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; });
}

function bindRowClicks(root) {
  $$("tr[data-s]", root).forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("button, a, input")) return;
    go(`#/sec/${encodeURIComponent(tr.dataset.s)}/des`);
  }));
}

function newsHtml(items) {
  if (!items.length) return `<div class="empty">No news.</div>`;
  return items.map((n) => `
    <div class="news-item"><span class="when">${esc(ago(n.time))}</span><span class="s">${esc(n.symbol)}</span>
    <span><a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener">${esc(n.title)}</a> <span class="pub">${esc(n.publisher || "")}</span></span></div>`).join("");
}

/* ---------- views: portfolio ---------- */

async function renderPortfolio() {
  view.innerHTML = `
    <div class="panel"><h3>Portfolio <span class="right">
      <a class="btn small" href="/api/portfolio/export">Export CSV</a>
      <button class="btn small" id="imp-toggle">Import CSV</button></span></h3>
      <form class="inline" id="add-pos">
        <label>Symbol <input name="symbol" required placeholder="SAP.DE" class="wide"></label>
        <label>Shares <input name="shares" type="number" step="any" min="0" required></label>
        <label>Buy price <input name="buy_price" type="number" step="any" min="0" required title="In the stock's trading currency"></label>
        <label>Date <input name="buy_date" type="date"></label>
        <label>Note <input name="note" class="wide"></label>
        <button class="btn">ADD POSITION</button>
        <span class="muted">Buy price in the stock's trading currency. Multiple buys = multiple lots.</span>
      </form>
      <form class="inline" id="imp" hidden>
        <textarea name="csv" rows="4" style="flex:1" placeholder="symbol,shares,buy_price,buy_date,note&#10;AAPL,10,150.5,2024-03-01,long term"></textarea>
        <button class="btn">IMPORT</button>
      </form>
      <div id="pf-totals" class="body"></div>
    </div>
    <div class="grid g2" style="margin-top:8px">
      <div class="panel"><h3>Positions</h3><div class="scroll" id="pf-table"><div class="empty">Loading...</div></div></div>
      <div class="panel"><h3>Allocation</h3><div class="body" id="pf-alloc"></div>
        <h3>Value history (1Y, today's FX)</h3><div class="chart small" id="pf-chart"></div></div>
    </div>`;
  $("#imp-toggle").addEventListener("click", () => { $("#imp").hidden = !$("#imp").hidden; });
  $("#add-pos").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    await load("ADD", () => api("/api/portfolio", { method: "POST", json: { ...f, symbol: f.symbol.toUpperCase(), shares: +f.shares, buy_price: +f.buy_price, buy_date: f.buy_date || null } }));
    renderPortfolio();
  });
  $("#imp").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await load("IMPORT", () => api("/api/portfolio/import", { method: "POST", body: e.target.csv.value, headers: { "Content-Type": "text/plain" } }));
    setStatus(`IMPORTED ${r.added} LOTS${r.errors.length ? " · ERRORS: " + r.errors.join("; ") : ""}`, r.errors.length ? "err" : "");
    if (r.added) renderPortfolio();
  });

  let s;
  try { s = await load("PORTFOLIO", () => api("/api/portfolio/summary")); } catch (e) { $("#pf-table").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; return; }
  const cur = s.base_currency;
  $("#pf-totals").innerHTML = `<div class="kv" style="grid-template-columns:repeat(4,minmax(0,1fr))">
    <div><span>Value</span><span>${money(s.total_value, cur)}</span></div>
    <div><span>Today</span><span class="${cls(s.day_change)}">${money(s.day_change, cur)} ${pct(s.day_change_pct)}</span></div>
    <div><span>Cost basis</span><span>${money(s.total_cost, cur)}</span></div>
    <div><span>Total P/L</span><span class="${cls(s.total_pl)}">${money(s.total_pl, cur)} ${pct(s.total_pl_pct)}</span></div></div>`;
  if (!s.positions.length) {
    $("#pf-table").innerHTML = `<div class="empty">No positions yet. Add your first holding above.</div>`;
    $("#pf-alloc").innerHTML = "";
    return;
  }
  $("#pf-table").innerHTML = `<table class="t"><thead><tr>
      <th>Symbol</th><th class="l">Name</th><th>Shares</th><th>Avg buy</th><th>Last</th><th>Day %</th>
      <th>Value ${esc(cur)}</th><th>P/L ${esc(cur)}</th><th>P/L %</th><th>Weight</th><th>P/E</th><th>RSI</th><th>Sig</th><th></th></tr></thead><tbody>
    ${s.positions.map((p) => `
      <tr class="click" data-s="${esc(p.symbol)}"><td class="sym">${esc(p.symbol)}</td><td class="name l">${esc(p.name)}</td>
      <td>${num(p.shares, p.shares % 1 ? 3 : 0)}</td><td>${price(p.avg_price)}</td><td>${price(p.price)} <span class="muted">${esc(p.currency || "")}</span></td>
      <td class="${cls(p.change_pct)}">${pct(p.change_pct)}</td><td>${num(p.value)}</td><td class="${cls(p.pl)}">${num(p.pl)}</td>
      <td class="${cls(p.pl_pct)}">${pct(p.pl_pct)}</td><td>${pct(p.weight, 1, false)}</td><td>${num(p.pe, 1)}</td><td>${num(p.rsi, 0)}</td>
      <td>${dot(p.light)}</td><td><button class="btn small" data-lots="${esc(p.symbol)}">LOTS</button></td></tr>
      <tr class="sub" data-sub="${esc(p.symbol)}" hidden><td colspan="14">${p.lots.map((l) => `
        <span style="margin-right:18px">#${l.id} ${num(l.shares, l.shares % 1 ? 3 : 0)} @ ${price(l.buy_price)} ${esc(l.buy_date || "")} ${esc(l.note || "")}
        <button class="btn small danger" data-del="${l.id}">DEL</button></span>`).join("")}</td></tr>`).join("")}
    </tbody></table>`;
  bindRowClicks($("#pf-table"));
  $$("[data-lots]").forEach((b) => b.addEventListener("click", () => {
    const row = $(`tr[data-sub="${CSS.escape(b.dataset.lots)}"]`);
    row.hidden = !row.hidden;
  }));
  $$("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Delete lot #${b.dataset.del}?`)) return;
    await load("DELETE", () => api(`/api/portfolio/${b.dataset.del}`, { method: "DELETE" }));
    renderPortfolio();
  }));
  const allocBlock = (title, list) => `<div class="amber" style="margin:4px 0">${title}</div>` + list.map((a) => `
    <div style="display:grid;grid-template-columns:150px 1fr 60px;gap:8px;align-items:center;margin:2px 0">
      <span class="muted" style="overflow:hidden;text-overflow:ellipsis">${esc(a.label)}</span><div class="bar"><i style="width:${a.pct}%"></i></div><span style="text-align:right">${num(a.pct, 1)}%</span></div>`).join("");
  $("#pf-alloc").innerHTML = allocBlock("BY SECTOR", s.by_sector) + allocBlock("BY CURRENCY", s.by_currency);
  api("/api/portfolio/history?period=1y").then((pts) => {
    const el = $("#pf-chart");
    if (!el || !pts.length) return;
    const ch = LWC.createChart(el, chartTheme());
    charts.push(ch);
    ch.addAreaSeries({ lineColor: "#ffa028", topColor: "rgba(255,160,40,.25)", bottomColor: "rgba(255,160,40,0)", lineWidth: 2 })
      .setData(pts.map((p) => ({ time: p.time, value: p.value })));
    ch.timeScale().fitContent();
  });
}

/* ---------- views: watchlist ---------- */

function watchTable(rows, editable = true) {
  return `<table class="t"><thead><tr><th>Symbol</th><th class="l">Name</th><th>Last</th><th>Day %</th><th>P/E</th><th>Fwd P/E</th>
    <th>Div</th><th>Mkt cap</th><th>52W range</th><th>RSI</th><th>Target</th><th>To target</th><th>Sig</th>${editable ? "<th></th>" : ""}</tr></thead><tbody>
    ${rows.map((r) => `<tr class="click ${r.target_hit ? "hit" : ""}" data-s="${esc(r.symbol)}" title="${esc(r.note || "")}">
      <td class="sym">${esc(r.symbol)}</td><td class="name l">${esc(r.name)}</td><td>${price(r.price)}</td>
      <td class="${cls(r.change_pct)}">${pct(r.change_pct)}</td><td>${num(r.pe, 1)}</td><td>${num(r.forward_pe, 1)}</td>
      <td>${frac(r.div_yield)}</td><td>${big(r.market_cap)}</td><td>${rangeBar(r.low52, r.high52, r.price)}</td>
      <td class="${r.rsi < 30 ? "up" : r.rsi > 70 ? "down" : ""}">${num(r.rsi, 0)}</td><td>${price(r.target_price)}</td>
      <td class="${r.target_hit ? "up" : ""}">${r.target_hit ? "HIT " : ""}${pct(r.to_target_pct, 1)}</td><td>${dot(r.light)}</td>
      ${editable ? `<td><button class="btn small danger" data-rm="${esc(r.symbol)}">DEL</button></td>` : ""}</tr>`).join("")}
    </tbody></table>`;
}

async function renderWatchlist() {
  view.innerHTML = `
    <div class="panel"><h3>Watchlist <span class="right muted">Green row = price at or below your target</span></h3>
      <form class="inline" id="add-w">
        <label>Symbol <input name="symbol" required class="wide" placeholder="MSFT"></label>
        <label>Target price <input name="target_price" type="number" step="any" min="0"></label>
        <label>Note <input name="note" class="wide"></label>
        <button class="btn">ADD / UPDATE</button>
      </form>
      <div class="scroll" id="wl"><div class="empty">Loading...</div></div></div>`;
  $("#add-w").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    await load("ADD", () => api("/api/watchlist", { method: "POST", json: { symbol: f.symbol.toUpperCase(), target_price: f.target_price ? +f.target_price : null, note: f.note } }));
    renderWatchlist();
  });
  try {
    const rows = await load("WATCHLIST", () => api("/api/watchlist/summary"));
    $("#wl").innerHTML = rows.length ? watchTable(rows) : `<div class="empty">Nothing on your watchlist yet.</div>`;
    bindRowClicks($("#wl"));
    $$("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
      await load("DELETE", () => api(`/api/watchlist/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }));
      renderWatchlist();
    }));
  } catch (e) { $("#wl").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

/* ---------- views: screener ---------- */

async function renderScreener() {
  const opts = await load("OPTIONS", () => api("/api/screener/options"));
  view.innerHTML = `
    <div class="panel"><h3>Equity screener <span class="right muted">Powered by Yahoo's screener · max 100 results</span></h3>
      <div class="toolbar" id="presets">${Object.entries(opts.presets).map(([k, v]) => `<button class="btn" data-p="${k}">${esc(v)}</button>`).join("")}</div>
      <form class="inline" id="scr">
        <label>Region <select name="region">${Object.entries(opts.regions).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
        <label>Sector <select name="sector"><option value="">All sectors</option>${opts.sectors.map((s) => `<option>${esc(s)}</option>`).join("")}</select></label>
        <label>P/E min <input name="pe_min" type="number" step="any"></label>
        <label>P/E max <input name="pe_max" type="number" step="any"></label>
        <label>Div % min <input name="div_min" type="number" step="any"></label>
        <label>ROE % min <input name="roe_min" type="number" step="any"></label>
        <label>Mkt cap min (bn USD) <input name="mcap_min" type="number" step="any"></label>
        <button class="btn">RUN</button>
      </form>
      <div class="scroll" id="scr-out"><div class="empty">Choose a preset or set filters and press RUN.</div></div></div>`;
  let preset = "";
  $$("#presets button").forEach((b) => b.addEventListener("click", () => {
    preset = preset === b.dataset.p ? "" : b.dataset.p;
    $$("#presets button").forEach((x) => x.classList.toggle("on", x.dataset.p === preset));
    runScreen();
  }));
  $("#scr").addEventListener("submit", (e) => { e.preventDefault(); runScreen(); });

  async function runScreen() {
    const f = Object.fromEntries(new FormData($("#scr")));
    const q = new URLSearchParams();
    if (preset) q.set("preset", preset);
    if (f.region) q.set("region", f.region);
    if (f.sector) q.set("sector", f.sector);
    ["pe_min", "pe_max"].forEach((k) => f[k] && q.set(k, f[k]));
    if (f.div_min) q.set("div_min", +f.div_min / 100);
    if (f.roe_min) q.set("roe_min", +f.roe_min / 100);
    if (f.mcap_min) q.set("mcap_min", +f.mcap_min * 1e9);
    $("#scr-out").innerHTML = `<div class="empty">Screening...</div>`;
    try {
      const r = await load("SCREEN", () => api(`/api/screener?${q}`));
      if (!r.results.length) { $("#scr-out").innerHTML = `<div class="empty">No matches.</div>`; return; }
      $("#scr-out").innerHTML = `<table class="t"><thead><tr><th>Symbol</th><th class="l">Name</th><th class="l">Exchange</th><th>Last</th><th>Day %</th>
        <th>Mkt cap</th><th>P/E</th><th>Fwd P/E</th><th>P/B</th><th>Div</th><th>52W range</th><th></th></tr></thead><tbody>
        ${r.results.map((x) => `<tr class="click" data-s="${esc(x.symbol)}"><td class="sym">${esc(x.symbol)}</td><td class="name l">${esc(x.name)}</td>
          <td class="l muted">${esc(x.exchange || "")}</td><td>${price(x.price)} <span class="muted">${esc(x.currency || "")}</span></td>
          <td class="${cls(x.change_pct)}">${pct(x.change_pct)}</td><td>${big(x.market_cap)}</td><td>${num(x.pe, 1)}</td><td>${num(x.forward_pe, 1)}</td>
          <td>${num(x.pb, 1)}</td><td>${frac(x.div_yield)}</td><td>${rangeBar(x.low52, x.high52, x.price)}</td>
          <td><button class="btn small" data-wl="${esc(x.symbol)}">+WL</button></td></tr>`).join("")}</tbody></table>`;
      bindRowClicks($("#scr-out"));
      $$("[data-wl]").forEach((b) => b.addEventListener("click", async () => {
        await load("ADD", () => api("/api/watchlist", { method: "POST", json: { symbol: b.dataset.wl } }));
        b.textContent = "✓"; b.disabled = true;
      }));
    } catch (e) { $("#scr-out").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
  }
}

/* ---------- views: news & help ---------- */

async function renderNews() {
  view.innerHTML = `<div class="panel"><h3>News · portfolio & watchlist <span class="right"><input id="nf" placeholder="Filter symbol / text"></span></h3>
    <div class="body" id="nl"><div class="empty">Loading...</div></div></div>`;
  try {
    const items = await load("NEWS", () => api("/api/news"));
    const draw = () => {
      const f = $("#nf").value.trim().toLowerCase();
      $("#nl").innerHTML = newsHtml(items.filter((n) => !f || n.symbol.toLowerCase().includes(f) || (n.title || "").toLowerCase().includes(f)));
    };
    $("#nf").addEventListener("input", draw);
    draw();
  } catch (e) { $("#nl").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

function renderHelp() {
  const rows = [
    ["SAP.DE <GO>", "Open a security (Yahoo symbol: .DE Xetra, .L London, .AS Amsterdam, .SW Swiss, .PA Paris; US without suffix)"],
    ["AAPL GP", "Price chart with SMA 50/200, volume and RSI"],
    ["AAPL FA", "Financial statements (annual / quarterly)"],
    ["AAPL EV", "Valuation history: P/E and P/S at each fiscal-year end"],
    ["AAPL CN", "Company news, upcoming earnings and dividend dates"],
    ["AAPL AI", "AI analysis of the latest annual report (needs an API key under Settings)"],
    ["MKT / PORT / WATCH / SCRN / NEWS", "Switch views (or F1-F5)"],
    ["OV", "Open the Overview page (or click OVERVIEW top right)"], ["SET", "Open Settings (or click SETTINGS top right)"],
    ["KEY", "Settings → AI model & API key"],
    ["Any letter or /", "Jumps into the command line"],
  ];
  view.innerHTML = `<div class="panel"><h3>Help</h3><div class="body"><table class="t help"><tbody>
    ${rows.map(([a, b]) => `<tr><td>${esc(a)}</td><td class="l">${esc(b)}</td></tr>`).join("")}</tbody></table>
    <p class="muted" style="white-space:normal;margin-top:12px">The signal check is a transparent checklist of common rules of thumb (trend, RSI, valuation versus history,
    balance sheet, analyst targets). It is meant to structure your own research. It is not a buy or sell recommendation.</p></div></div>`;
}

/* ---------- security detail ---------- */

async function renderSecurity(sym, tab) {
  view.innerHTML = `<div class="hdr" id="hdr"><span class="sym">${esc(sym)}</span><span class="nm">Loading...</span></div>
    <div class="tabs">${TABS.map(([k, code, label]) => `<button data-t="${k}" class="${k === tab ? "active" : ""}"><b>${code}</b>${label}</button>`).join("")}
    <button id="wl-add" style="margin-left:auto"><b>+</b>Watchlist</button></div><div id="tab"></div>`;
  $$(".tabs button[data-t]").forEach((b) => b.addEventListener("click", () => go(`#/sec/${encodeURIComponent(sym)}/${b.dataset.t}`)));
  $("#wl-add").addEventListener("click", async () => {
    await load("ADD", () => api("/api/watchlist", { method: "POST", json: { symbol: sym } }));
    setStatus(`${sym} ADDED TO WATCHLIST`);
  });
  let info;
  try { info = await load(sym, () => api(`/api/quote/${encodeURIComponent(sym)}`)); } catch (e) {
    $("#hdr").innerHTML = `<span class="sym">${esc(sym)}</span><span class="err">Unknown symbol or no data (${esc(e.message)})</span>`;
    return;
  }
  $("#hdr").innerHTML = `<span class="sym">${esc(sym)}</span><span class="nm">${esc(info.name)}</span>
    <span class="px">${price(info.price)} <span class="muted" style="font-size:12px">${esc(info.currency || "")}</span></span>
    <span class="${cls(info.change)}">${isNum(info.change) ? (info.change > 0 ? "+" : "") + price(info.change) : ""} ${pct(info.change_pct)}</span>
    <span class="muted">${esc(info.exchange || "")} · ${esc(info.sector || info.quote_type || "")}${info.industry ? " · " + esc(info.industry) : ""}</span>`;
  const el = $("#tab");
  ({ des: tabDes, gp: tabChart, fa: tabFa, ev: tabEv, cn: tabCn, ai: tabAi })[tab](sym, info, el);
}

function priceChart(container, rsiContainer) {
  const ch = LWC.createChart(container, chartTheme());
  charts.push(ch);
  const candles = ch.addCandlestickSeries({ upColor: "#3ddc84", downColor: "#ff5a5a", wickUpColor: "#3ddc84", wickDownColor: "#ff5a5a", borderVisible: false });
  const s50 = ch.addLineSeries({ color: "#5fd7ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const s200 = ch.addLineSeries({ color: "#ffa028", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const vol = ch.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, color: "#333" });
  ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  let rsiSeries = null;
  if (rsiContainer) {
    const rc = LWC.createChart(rsiContainer, chartTheme({ timeScale: { visible: false } }));
    charts.push(rc);
    rsiSeries = rc.addLineSeries({ color: "#ffe066", lineWidth: 1, priceLineVisible: false });
    rsiSeries.createPriceLine({ price: 70, color: "#ff5a5a", lineStyle: 2, lineWidth: 1, axisLabelVisible: true });
    rsiSeries.createPriceLine({ price: 30, color: "#3ddc84", lineStyle: 2, lineWidth: 1, axisLabelVisible: true });
    ch.timeScale().subscribeVisibleLogicalRangeChange((r) => r && rc.timeScale().setVisibleLogicalRange(r));
  }
  return {
    set(rows) {
      candles.setData(rows.map((r) => ({ time: r.time, open: r.open, high: r.high, low: r.low, close: r.close })));
      s50.setData(rows.filter((r) => isNum(r.sma50)).map((r) => ({ time: r.time, value: r.sma50 })));
      s200.setData(rows.filter((r) => isNum(r.sma200)).map((r) => ({ time: r.time, value: r.sma200 })));
      vol.setData(rows.map((r) => ({ time: r.time, value: r.volume || 0, color: r.close >= r.open ? "rgba(61,220,132,.35)" : "rgba(255,90,90,.35)" })));
      if (rsiSeries) rsiSeries.setData(rows.map((r) => (isNum(r.rsi) ? { time: r.time, value: r.rsi } : { time: r.time })));
      ch.timeScale().fitContent();
    },
  };
}

function periodBar(onPick, initial = "1y") {
  const periods = ["1d", "5d", "1mo", "6mo", "ytd", "1y", "2y", "5y", "max"];
  const bar = document.createElement("div");
  bar.className = "toolbar";
  bar.innerHTML = periods.map((p) => `<button class="btn small ${p === initial ? "on" : ""}" data-p="${p}">${p.toUpperCase()}</button>`).join("")
    + `<span class="muted" style="margin-left:auto">SMA50 <span style="color:#5fd7ff">━</span> SMA200 <span class="amber">━</span> RSI(14) below</span>`;
  $$("button", bar).forEach((b) => b.addEventListener("click", () => {
    $$("button", bar).forEach((x) => x.classList.toggle("on", x === b));
    onPick(b.dataset.p);
  }));
  return bar;
}

function keyStats(i) {
  const c = i.currency || "";
  const rows = [
    ["Market cap", big(i.market_cap, c)], ["P/E (TTM)", num(i.pe, 1)], ["Forward P/E", num(i.forward_pe, 1)], ["PEG", num(i.peg, 2)],
    ["Price / book", num(i.pb, 2)], ["Price / sales", num(i.ps, 2)], ["EV / EBITDA", num(i.ev_ebitda, 1)], ["Dividend yield", frac(i.div_yield, 2)],
    ["Payout ratio", frac(i.payout)], ["Gross margin", frac(i.gross_margin)], ["Operating margin", frac(i.op_margin)], ["Net margin", frac(i.net_margin)],
    ["ROE", frac(i.roe)], ["ROA", frac(i.roa)], ["Debt / equity", num(i.debt_to_equity, 2)], ["Current ratio", num(i.current_ratio, 2)],
    ["Revenue growth", frac(i.revenue_growth)], ["Earnings growth", frac(i.earnings_growth)], ["Free cash flow", big(i.fcf)], ["Beta", num(i.beta, 2)],
    ["52W high", price(i.high52)], ["52W low", price(i.low52)], ["EPS (TTM)", price(i.eps)], ["Forward EPS", price(i.forward_eps)],
    ["Analyst target", `${price(i.target_mean)} <span class="muted">(${price(i.target_low)}–${price(i.target_high)})</span>`],
    ["Recommendation", `${esc((i.recommendation || "–").toUpperCase())} <span class="muted">(${i.analysts || 0})</span>`],
    ["Employees", isNum(i.employees) ? num(i.employees, 0) : "–"], ["Country", esc(i.country || "–")],
  ];
  return `<div class="kv">${rows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("")}</div>`;
}

function signalTable(sig) {
  return `<table class="t sig"><tbody>${sig.items.map((x) => `<tr><td>${dot(x.status)}</td><td class="l">${esc(x.label)}</td>
    <td class="l amber">${esc(x.value)}</td><td class="note">${esc(x.note)}</td></tr>`).join("")}</tbody></table>
    <div class="muted" style="padding:6px 8px;white-space:normal">${sig.good} positive · ${sig.bad} negative${isNum(sig.score) ? ` · score ${sig.score}/100` : ""}. ${esc(sig.disclaimer)}</div>`;
}

async function tabDes(sym, info, el) {
  el.innerHTML = `<div class="grid g2">
    <div class="panel"><h3>Price</h3><div id="pb"></div><div class="chart" id="pc"></div><div class="chart rsi" id="rc"></div></div>
    <div class="panel"><h3>Key figures</h3><div class="body">${keyStats(info)}</div></div></div>
    <div class="grid g2" style="margin-top:8px">
      <div class="panel"><h3>Signal check <span class="right muted">not investment advice</span></h3><div id="sig"><div class="empty">Loading...</div></div></div>
      <div class="panel"><h3>Description</h3><div class="body" style="white-space:normal;color:#bbb">${esc(info.description || "No description available.")}
        ${info.website ? `<p><a href="${esc(safeUrl(info.website))}" target="_blank" rel="noopener">${esc(info.website)}</a></p>` : ""}</div></div></div>`;
  const pc = priceChart($("#pc"), $("#rc"));
  const loadP = (p) => api(`/api/history/${encodeURIComponent(sym)}?period=${p}`).then((rows) => pc.set(rows)).catch((e) => setStatus(e.message, "err"));
  $("#pb").appendChild(periodBar(loadP));
  loadP("1y");
  api(`/api/signals/${encodeURIComponent(sym)}`).then((s) => { $("#sig").innerHTML = signalTable(s); })
    .catch((e) => { $("#sig").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; });
}

async function tabChart(sym, info, el) {
  el.innerHTML = `<div class="panel"><h3>Chart</h3><div id="pb"></div><div class="chart" id="pc" style="height:calc(100vh - 330px);min-height:320px"></div><div class="chart rsi" id="rc"></div></div>`;
  const pc = priceChart($("#pc"), $("#rc"));
  const loadP = (p) => load("CHART", () => api(`/api/history/${encodeURIComponent(sym)}?period=${p}`)).then((rows) => pc.set(rows)).catch(() => {});
  $("#pb").appendChild(periodBar(loadP));
  loadP("1y");
}

async function tabFa(sym, info, el, freq = "annual") {
  el.innerHTML = `<div class="panel"><h3>Financial statements <span class="right">
    <button class="btn small ${freq === "annual" ? "on" : ""}" data-f="annual">ANNUAL</button>
    <button class="btn small ${freq === "quarterly" ? "on" : ""}" data-f="quarterly">QUARTERLY</button></span></h3>
    <div class="scroll" id="fa"><div class="empty">Loading...</div></div></div>`;
  $$("[data-f]", el).forEach((b) => b.addEventListener("click", () => tabFa(sym, info, el, b.dataset.f)));
  try {
    const f = await load("FINANCIALS", () => api(`/api/financials/${encodeURIComponent(sym)}?freq=${freq}`));
    if (!f.periods.length) { $("#fa").innerHTML = `<div class="empty">No statements available for this symbol.</div>`; return; }
    const cur = info.financial_currency || info.currency || "";
    const fmt = (r, v) => (r.kind === "pct" ? frac(v) : r.kind === "eps" ? price(v) : big(v));
    $("#fa").innerHTML = f.sections.map((s) => `<table class="t fixed" style="margin-bottom:10px"><thead><tr><th>${esc(s.title.toUpperCase())} <span class="muted">${esc(cur)}</span></th>
      ${f.periods.map((p) => `<th>${esc(p)}</th>`).join("")}</tr></thead><tbody>
      ${s.rows.map((r) => `<tr><td class="l">${esc(r.label)}</td>${r.values.map((v, i) => `<td>${fmt(r, v)}${isNum(r.growth[i])
        ? ` <span class="${cls(r.growth[i])}" style="font-size:11px">${pct(r.growth[i] * 100, 0)}</span>` : ""}</td>`).join("")}</tr>`).join("")}
      </tbody></table>`).join("") + `<div class="muted" style="padding:6px 8px">Source: Yahoo Finance. Percentages next to values = change vs. previous period.</div>`;
  } catch (e) { $("#fa").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

async function tabEv(sym, info, el) {
  el.innerHTML = `<div class="grid g2"><div class="panel"><h3>P/E at fiscal-year end vs. today</h3><div class="chart small" id="evc"></div></div>
    <div class="panel"><h3>Valuation history</h3><div id="evt" class="scroll"><div class="empty">Loading...</div></div></div></div>`;
  try {
    const v = await load("VALUATION", () => api(`/api/valuation/${encodeURIComponent(sym)}`));
    const pts = v.points.filter((p) => isNum(p.pe));
    $("#evt").innerHTML = `<div class="body kv" style="grid-template-columns:repeat(2,minmax(0,1fr))">
        <div><span>P/E today</span><span class="amber">${num(v.current_pe, 1)}</span></div><div><span>Median P/E</span><span>${num(v.pe_median, 1)}</span></div>
        <div><span>Lowest</span><span>${num(v.pe_min, 1)}</span></div><div><span>Highest</span><span>${num(v.pe_max, 1)}</span></div>
        <div><span>P/S today</span><span class="amber">${num(v.current_ps, 2)}</span></div><div><span>Median P/S</span><span>${num(v.ps_median, 2)}</span></div></div>
      <table class="t"><thead><tr><th>FY end</th><th>Price</th><th>EPS</th><th>P/E</th><th>P/S</th></tr></thead><tbody>
      ${v.points.slice().reverse().map((p) => `<tr><td>${esc(p.date)}</td><td>${price(p.price)}</td><td>${price(p.eps)}</td><td>${num(p.pe, 1)}</td><td>${num(p.ps, 2)}</td></tr>`).join("")}
      </tbody></table><div class="muted" style="padding:6px 8px;white-space:normal">Source: ${esc(v.source)}. P/E = closing price at fiscal-year end / diluted EPS of that year.
      A P/E well below its own median can hint at a cheap valuation - or at falling earnings expectations.</div>`;
    if (!pts.length) { $("#evc").innerHTML = `<div class="empty">No positive earnings history to chart.</div>`; return; }
    const ch = LWC.createChart($("#evc"), chartTheme({ timeScale: { borderColor: "#2a2a2a", timeVisible: false } }));
    charts.push(ch);
    const s = ch.addLineSeries({ color: "#ffa028", lineWidth: 2, pointMarkersVisible: true });
    s.setData(pts.map((p) => ({ time: p.date, value: p.pe })));
    if (isNum(v.pe_median)) s.createPriceLine({ price: v.pe_median, color: "#8a8a8a", lineStyle: 2, title: "median" });
    if (isNum(v.current_pe)) s.createPriceLine({ price: v.current_pe, color: "#5fd7ff", lineStyle: 0, title: "today" });
    ch.timeScale().fitContent();
  } catch (e) { $("#evt").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

async function tabCn(sym, info, el) {
  el.innerHTML = `<div class="grid g2"><div class="panel"><h3>Company news</h3><div class="body" id="cn"><div class="empty">Loading...</div></div></div>
    <div class="panel"><h3>Events</h3><div class="body" id="ev"><div class="empty">Loading...</div></div></div></div>`;
  api(`/api/news/${encodeURIComponent(sym)}`).then((n) => { $("#cn").innerHTML = newsHtml(n); })
    .catch((e) => { $("#cn").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; });
  api(`/api/calendar/${encodeURIComponent(sym)}`).then((c) => {
    $("#ev").innerHTML = `<div class="kv" style="grid-template-columns:1fr"><div><span>Next earnings</span><span class="amber">${esc(c.earnings_date || "–")}</span></div>
      <div><span>Ex-dividend date</span><span>${esc(c.ex_dividend_date || "–")}</span></div><div><span>Dividend payment</span><span>${esc(c.dividend_date || "–")}</span></div></div>`;
  }).catch((e) => { $("#ev").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; });
}

async function tabAi(sym, info, el) {
  el.innerHTML = `<div class="grid g2">
    <div class="panel"><h3>AI annual-report analysis <span class="right">
      <select id="lens"><option value="general">Balanced view</option><option value="value">Value lens (Graham)</option><option value="growth">Growth / quality lens</option></select>
      <button class="btn small" id="gen">GENERATE</button><button class="btn small" id="regen">REFRESH</button></span></h3>
      <div id="ai-status" class="muted" style="padding:4px 12px"></div><div class="ai" id="ai-out"></div></div>
    <div class="panel"><h3>Sources</h3><div class="body" id="ai-src"><div class="muted">Sources appear here after an analysis.</div></div>
      <h3>Ask about ${esc(sym)}</h3><div class="chat" id="chat"></div>
      <form class="inline" id="ask"><input name="q" style="flex:1" placeholder="e.g. How dependent is the company on China?" required><button class="btn">ASK</button></form></div></div>`;
  const out = $("#ai-out"), st = $("#ai-status");
  $("#lens").value = defaultLens;
  let sources = [], text = "";
  const history = [];
  const showSources = () => {
    $("#ai-src").innerHTML = sources.length ? sources.map((s) => `<div><span class="amber">[${esc(s.id)}]</span> <a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">${esc(s.title)}</a> <span class="muted">${esc(s.source)}</span></div>`).join("")
      : `<div class="muted">No report document found; the analysis is based on key figures only.</div>`;
  };
  async function run(refresh) {
    if (aiAbort) aiAbort.abort();
    aiAbort = new AbortController();
    text = ""; out.innerHTML = ""; st.innerHTML = `<span class="blink">▮</span> starting...`;
    try {
      await stream(`/api/ai/report/${encodeURIComponent(sym)}?lens=${$("#lens").value}&refresh=${refresh}`, null, (ev) => {
        if (ev.type === "status") st.textContent = ev.text;
        if (ev.type === "sources") { sources = ev.sources; showSources(); if (ev.cached) st.textContent = `Cached analysis from ${ev.cached}. Press REFRESH for a new one.`; }
        if (ev.type === "delta") { text += ev.text; out.innerHTML = markdown(text, sources); }
        if (ev.type === "error") { st.innerHTML = `<span class="err">${esc(ev.text)}</span> ${ev.text.includes("Settings") ? '<a href="/settings#ai">Open settings</a>' : ""}`; }
        if (ev.type === "done" && !st.textContent.startsWith("Cached")) st.textContent = "Done. AI output can contain errors - check the cited sources.";
      }, aiAbort.signal);
    } catch (e) { if (e.name !== "AbortError") st.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }
  $("#gen").addEventListener("click", () => run(false));
  $("#regen").addEventListener("click", () => run(true));
  const cached = await api(`/api/ai/report/${encodeURIComponent(sym)}?lens=${defaultLens}`).catch(() => null);
  if (cached) {
    sources = cached.sources; showSources();
    out.innerHTML = markdown(cached.content, sources);
    st.textContent = `Cached analysis from ${cached.created}. Press REFRESH for a new one.`;
  } else {
    st.textContent = "Press GENERATE to find the latest annual report and analyse it with your LLM.";
  }
  $("#ask").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = e.target.q.value.trim();
    if (!q) return;
    e.target.q.value = "";
    const chat = $("#chat");
    chat.insertAdjacentHTML("beforeend", `<div class="q">${esc(q)}</div><div class="ai a"><span class="blink">▮</span></div>`);
    const box = chat.lastElementChild;
    let answer = "", src = [];
    try {
      await stream(`/api/ai/ask/${encodeURIComponent(sym)}`, { question: q, history }, (ev) => {
        if (ev.type === "sources") src = ev.sources;
        if (ev.type === "delta") { answer += ev.text; box.innerHTML = markdown(answer, src); }
        if (ev.type === "error") box.innerHTML = `<span class="err">${esc(ev.text)}</span>`;
      });
      history.push({ role: "user", content: q }, { role: "assistant", content: answer });
    } catch (err) { box.innerHTML = `<span class="err">${esc(err.message)}</span>`; }
  });
}

/* ---------- boot ---------- */

api("/api/status").then((s) => {
  baseCurrency = s.base_currency;
  defaultLens = s.ai_lens || "general";
  if (s.refresh_minutes > 0) {
    // Re-render list views periodically; detail pages and forms are left alone so nothing you type is lost.
    setInterval(() => {
      const v = location.hash.replace(/^#\/?/, "").split("/")[0] || "market";
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
      if (["market", "watchlist", "news"].includes(v) && !typing) route();
    }, s.refresh_minutes * 60_000);
  }
  const tag = $("#provider");
  tag.textContent = s.demo ? "DEMO DATA" : s.provider.toUpperCase();
  tag.classList.toggle("demo", s.demo);
}).catch(() => {});
route();
