/* Flower Settings page. */
"use strict";

const { api, esc } = Flower;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const KEY_HELP = {
  deepseek: "Create a key at platform.deepseek.com (API keys).",
  openai: "Create a key at platform.openai.com (API keys).",
  anthropic: "Create a key at console.anthropic.com (API keys).",
  gemini: "Create a key in Google AI Studio (aistudio.google.com).",
  openrouter: "Create a key at openrouter.ai (Keys).",
  groq: "Create a key at console.groq.com (API keys).",
  ollama: "No key needed. Ollama must run on the same machine as Flower (ollama.com).",
  custom: "Any service that speaks the OpenAI chat-completions API. Enter its base URL and model.",
};
const DEFAULT_MARKET = "^GSPC, ^NDX, ^GDAXI, ^STOXX50E, ^N225, ^VIX, ^TNX, EURUSD=X, GC=F, CL=F, BTC-USD";
let S = null;

/* ---------- helpers ---------- */

function toast(text, bad = false) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2600);
}

function setChoice(name, value) {
  $$(`[data-name="${name}"] button`).forEach((b) => b.classList.toggle("on", b.dataset.v === String(value)));
}
function getChoice(name) {
  const b = $(`[data-name="${name}"] button.on`);
  return b ? b.dataset.v : null;
}
$$("[data-name] button").forEach((b) => b.addEventListener("click", () => {
  setChoice(b.closest("[data-name]").dataset.name, b.dataset.v);
  if (b.closest("[data-name]").dataset.name === "theme") applyTheme(b.dataset.v);
  if (b.closest("[data-name]").dataset.name === "search_provider") updateSearchKeyField();
}));

function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem("flower-theme", t); } catch (_) { /* storage blocked */ }
}

$$("[data-show]").forEach((b) => b.addEventListener("click", () => {
  const input = $(`#${b.dataset.show}`);
  input.type = input.type === "password" ? "text" : "password";
  b.textContent = input.type === "password" ? "Show" : "Hide";
}));

async function save(body, okText = "Saved") {
  try {
    S = await api("/api/settings", { method: "PUT", json: body });
    fill();
    toast(okText);
    return true;
  } catch (e) {
    toast(e.message.replace(/^\d+: /, ""), true);
    return false;
  }
}

/* ---------- fill from server ---------- */

function fill() {
  setChoice("start_page", S.start_page);
  setChoice("theme", S.theme);
  applyTheme(S.theme);
  $("#base_currency").innerHTML = S.currencies.map((c) => `<option>${c}</option>`).join("");
  $("#base_currency").value = S.base_currency;
  $("#refresh_minutes").value = String(S.refresh_minutes);

  setChoice("data_mode", S.data_mode);
  $("#market_symbols").value = S.market_symbols.join(", ");
  $("#sec_contact").value = S.sec_contact;

  $("#llm_provider").innerHTML = Object.entries(S.llm_presets).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("");
  $("#llm_provider").value = S.llm_provider;
  $("#llm_model").value = S.llm_model;
  $("#llm_base_url").value = S.llm_base_url;
  $("#ai_language").value = S.ai_language;
  $("#ai_lens").value = S.ai_lens;
  $("#keyHint").textContent = S.llm_key_hint ? `stored key ends with ${S.llm_key_hint}` : "no key stored";
  $("#keyHelp").textContent = KEY_HELP[S.llm_provider] || "";
  const badge = $("#aiBadge");
  badge.textContent = S.llm_configured ? "Configured" : "Not configured";
  badge.className = "badge " + (S.llm_configured ? "ok" : "warn");

  setChoice("search_provider", S.search_provider);
  $("#skeyHint").textContent = S.search_key_hint ? `stored key ends with ${S.search_key_hint}` : "no key stored";
  updateSearchKeyField();

  const src = S.login_source;
  const lb = $("#loginBadge");
  lb.textContent = src ? "Login on" : "No login";
  lb.className = "badge " + (src ? "ok" : "warn");
  if (src === "env") {
    $("#loginText").innerHTML = `Login is set in the server's <code>.env</code> file (user <b>${esc(S.login_user)}</b>). Change or remove it there and restart Flower.`;
    $("#loginFields").hidden = true;
  } else {
    $("#loginFields").hidden = false;
    $("#lu").value = S.login_user || $("#lu").value;
    $("#removeLogin").hidden = !src;
    $("#loginText").innerHTML = src
      ? `Flower asks for user <b>${esc(S.login_user)}</b> and a password. You can change the password below.`
      : `Anyone who can reach this Flower can see your portfolio. That's fine on your own PC. <b>Set a login before you put Flower on a server.</b>`
        + (S.server.local_request ? "" : " A first login can only be set from the machine Flower runs on.");
  }

  $("#aboutList").innerHTML = [
    ["Data", S.data_mode === "demo" ? "Demo (synthetic sample data)" : "Live (Yahoo Finance, SEC EDGAR)"],
    ["Listening on", `${S.server.host}:${S.server.port}`],
    ["Database", S.server.db_path],
    ["API docs", `<a href="/api/docs">/api/docs</a>`],
  ].map(([k, v]) => `<dt>${k}</dt><dd>${k === "API docs" ? v : esc(v)}</dd>`).join("");
}

function updateSearchKeyField() {
  const p = getChoice("search_provider");
  $("#searchKeyField").hidden = p === "duckduckgo";
  $("#clearSKey").hidden = p === "duckduckgo" && !S?.search_key_hint;
}

/* ---------- section forms ---------- */

$("#general").addEventListener("submit", (e) => {
  e.preventDefault();
  save({ start_page: getChoice("start_page"), theme: getChoice("theme"), base_currency: $("#base_currency").value,
         refresh_minutes: +$("#refresh_minutes").value });
});

$("#data").addEventListener("submit", (e) => {
  e.preventDefault();
  save({ data_mode: getChoice("data_mode"), market_symbols: $("#market_symbols").value, sec_contact: $("#sec_contact").value.trim() });
});
$("#resetMarket").addEventListener("click", () => { $("#market_symbols").value = DEFAULT_MARKET; });
$("#clearCache").addEventListener("click", async () => {
  await api("/api/settings/clear-cache", { method: "POST" });
  toast("Cache cleared. Prices will reload");
});

$("#llm_provider").addEventListener("change", (e) => {
  const p = S.llm_presets[e.target.value];
  $("#llm_model").value = p.model;
  $("#llm_base_url").value = p.base_url;
  $("#keyHelp").textContent = KEY_HELP[e.target.value] || "";
});
$("#ai").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { llm_provider: $("#llm_provider").value, llm_model: $("#llm_model").value, llm_base_url: $("#llm_base_url").value,
                 ai_language: $("#ai_language").value, ai_lens: $("#ai_lens").value };
  const key = $("#llm_api_key").value.trim();
  if (key) body.llm_api_key = key;
  if (await save(body, key ? "Saved. API key stored" : "Saved")) $("#llm_api_key").value = "";
});
$("#clearKey").addEventListener("click", () => {
  if (confirm("Remove the stored API key?")) save({ clear_llm_key: true }, "API key removed");
});
$("#testLlm").addEventListener("click", async () => {
  const out = $("#llmResult");
  out.innerHTML = `<span class="muted">Testing… (unsaved changes are not used; save first)</span>`;
  const r = await api("/api/settings/test-llm", { method: "POST" }).catch((err) => ({ ok: false, error: err.message }));
  out.innerHTML = r.ok ? `<span class="ok">Connected. The model replied: "${esc(r.reply)}"</span>` : `<span class="err">${esc(r.error)}</span>`;
});

$("#search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { search_provider: getChoice("search_provider") };
  const key = $("#search_api_key").value.trim();
  if (key) body.search_api_key = key;
  if (await save(body, key ? "Saved. Search key stored" : "Saved")) $("#search_api_key").value = "";
});
$("#clearSKey").addEventListener("click", () => save({ clear_search_key: true }, "Search key removed"));

$("#security").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#loginResult");
  const u = $("#lu").value.trim(), p = $("#lp").value, p2 = $("#lp2").value;
  if (!u) { out.innerHTML = `<span class="err">Enter a username.</span>`; return; }
  if (p.length < 8) { out.innerHTML = `<span class="err">The password needs at least 8 characters.</span>`; return; }
  if (p !== p2) { out.innerHTML = `<span class="err">The passwords don't match.</span>`; return; }
  try {
    await api("/api/settings/login", { method: "PUT", json: { username: u, password: p } });
    out.innerHTML = `<span class="ok">Login saved. Your browser will now ask for it. Use user "${esc(u)}" and your new password.</span>`;
    $("#lp").value = ""; $("#lp2").value = "";
    S = await api("/api/settings");
    fill();
  } catch (err) { out.innerHTML = `<span class="err">${esc(err.message.replace(/^\d+: /, ""))}</span>`; }
});
$("#removeLogin").addEventListener("click", async () => {
  if (!confirm("Remove the login? Anyone who can reach Flower will see your portfolio.")) return;
  try {
    await api("/api/settings/login", { method: "DELETE" });
    S = await api("/api/settings");
    fill();
    toast("Login removed");
  } catch (err) { toast(err.message, true); }
});

$("#importFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const out = $("#importResult");
  try {
    const r = await api("/api/portfolio/import", { method: "POST", body: await file.text(), headers: { "Content-Type": "text/plain" } });
    out.innerHTML = `<span class="ok">Imported ${r.added} lot(s).</span>` + (r.errors.length ? `<br><span class="err">${r.errors.map(esc).join("<br>")}</span>` : "");
  } catch (err) { out.innerHTML = `<span class="err">${esc(err.message)}</span>`; }
  e.target.value = "";
});

/* ---------- side navigation highlight ---------- */

const links = $$(".side a");
const observer = new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (en.isIntersecting) links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === `#${en.target.id}`));
  });
}, { rootMargin: "-30% 0px -60% 0px" });
$$(".card[id]").forEach((c) => observer.observe(c));

api("/api/settings").then((s) => { S = s; fill(); }).catch((e) => toast(e.message, true));
