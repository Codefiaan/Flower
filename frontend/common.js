/* Shared helpers for all Flower pages: API calls, number formatting, markdown, AI streams. */
"use strict";

const Flower = (() => {
  async function api(path, opts = {}) {
    const init = { ...opts, headers: { ...(opts.headers || {}) } };
    if (opts.json !== undefined) {
      init.body = JSON.stringify(opts.json);
      init.headers["Content-Type"] = "application/json";
      delete init.json;
    }
    const res = await fetch(path, init);
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = j.detail || JSON.stringify(j); } catch (_) { /* not json */ }
      throw new Error(`${res.status}: ${msg}`);
    }
    const type = res.headers.get("content-type") || "";
    return type.includes("json") ? res.json() : res.text();
  }

  const isNum = (v) => typeof v === "number" && isFinite(v);

  function num(v, digits = 2) {
    if (!isNum(v)) return "–";
    return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function price(v) {
    if (!isNum(v)) return "–";
    const d = Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 4;
    return num(v, d);
  }

  function pct(v, digits = 2, sign = true) {
    if (!isNum(v)) return "–";
    return (sign && v > 0 ? "+" : "") + num(v, digits) + "%";
  }

  function frac(v, digits = 1) { // fraction -> percent string
    return isNum(v) ? num(v * 100, digits) + "%" : "–";
  }

  function big(v, cur = "") {
    if (!isNum(v)) return "–";
    const a = Math.abs(v);
    const s = a >= 1e12 ? num(v / 1e12, 2) + "T" : a >= 1e9 ? num(v / 1e9, 2) + "B" : a >= 1e6 ? num(v / 1e6, 1) + "M"
      : a >= 1e3 ? num(v / 1e3, 1) + "K" : num(v, 0);
    return cur ? `${s} ${cur}` : s;
  }

  function money(v, cur = "EUR", digits = 2) {
    if (!isNum(v)) return "–";
    try {
      return v.toLocaleString("en-US", { style: "currency", currency: cur, minimumFractionDigits: digits, maximumFractionDigits: digits });
    } catch (_) {
      return `${num(v, digits)} ${cur}`;
    }
  }

  const cls = (v) => (!isNum(v) || v === 0 ? "flat" : v > 0 ? "up" : "down");

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function ago(epoch) {
    if (!epoch) return "";
    const s = Date.now() / 1000 - epoch;
    if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }

  function safeUrl(u) {
    return /^https?:\/\//i.test(u || "") ? u : "#";
  }

  /* Minimal, safe markdown: headings, lists, bold/italic, code, links, tables, [S1] citations. */
  function markdown(src, sources = []) {
    const srcMap = Object.fromEntries((sources || []).map((s) => [s.id, s]));
    const inline = (t) => esc(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`)
      .replace(/\[(S\d+)\]/g, (m, id) => srcMap[id]
        ? `<a class="cite" href="${esc(safeUrl(srcMap[id].url))}" target="_blank" rel="noopener" title="${esc(srcMap[id].title)}">[${id}]</a>` : m);
    const lines = String(src || "").split("\n");
    let html = "", list = null, table = null;
    const close = () => {
      if (list) { html += `</${list}>`; list = null; }
      if (table) { html += "</tbody></table>"; table = null; }
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      let m;
      if (/^\s*\|.*\|\s*$/.test(line)) {
        const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
        if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
        if (!table) { close(); html += "<table class=\"md-table\"><thead><tr>" + cells.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>"; table = true; continue; }
        html += "<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
        continue;
      }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { close(); const l = Math.min(m[1].length + 1, 5); html += `<h${l}>${inline(m[2])}</h${l}>`; continue; }
      if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; continue; }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; continue; }
      if (!line.trim()) { close(); continue; }
      close();
      html += `<p>${inline(line)}</p>`;
    }
    close();
    return html;
  }

  /* POST to an NDJSON streaming endpoint and call onEvent for every event. */
  async function stream(path, body, onEvent, signal) {
    const res = await fetch(path, {
      method: "POST", signal,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
    if (buf.trim()) onEvent(JSON.parse(buf));
  }

  /* Small canvas sparkline, no library needed. */
  function sparkline(canvas, values, color) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 100, h = canvas.clientHeight || 30;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    const v = (values || []).filter(isNum);
    if (v.length < 2) return;
    const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
    ctx.strokeStyle = color || (v[v.length - 1] >= v[0] ? "#3ddc84" : "#ff5a5a");
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    v.forEach((y, i) => {
      const px = (i / (v.length - 1)) * (w - 2) + 1;
      const py = h - 2 - ((y - min) / span) * (h - 4);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    ctx.stroke();
  }

  function debounce(fn, ms = 250) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  return { api, num, price, pct, frac, big, money, cls, esc, ago, safeUrl, markdown, stream, sparkline, debounce, isNum };
})();
