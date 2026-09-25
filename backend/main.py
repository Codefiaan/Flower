"""Flower Terminal - FastAPI app serving the JSON API and the static frontend."""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, db, llm, portfolio, reports, screener
from .cache import ttl_cache
from .config import ROOT, settings
from .providers import get_provider
from .providers import sec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("flower")

FRONTEND = ROOT / "frontend"

MARKET = ["^GSPC", "^NDX", "^GDAXI", "^STOXX50E", "^N225", "^VIX", "^TNX", "EURUSD=X", "GC=F", "CL=F", "BTC-USD"]
MARKET_NAMES = {"^GSPC": "S&P 500", "^NDX": "Nasdaq 100", "^GDAXI": "DAX", "^STOXX50E": "Euro Stoxx 50",
                "^N225": "Nikkei 225", "^VIX": "VIX", "^TNX": "US 10Y yield", "EURUSD=X": "EUR/USD",
                "GC=F": "Gold", "CL=F": "WTI crude", "BTC-USD": "Bitcoin"}

app = FastAPI(title="Flower Terminal", docs_url="/api/docs", openapi_url="/api/openapi.json")
db.init()


# --- auth ----------------------------------------------------------------------

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not settings.password:
        # Safety net: a request that came through a reverse proxy means the app is exposed
        # beyond this machine, so refuse to serve private portfolio data without a login.
        if request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
            return Response("Flower is reachable through a proxy but no login is set. "
                            "Set FLOWER_USER and FLOWER_PASSWORD in .env and restart.", status_code=403)
        return await call_next(request)
    header = request.headers.get("authorization", "")
    ok = False
    if header.lower().startswith("basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
            ok = secrets.compare_digest(user, settings.user or "flower") and secrets.compare_digest(pw, settings.password)
        except Exception:
            ok = False
    if not ok:
        return Response("Authentication required", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Flower Terminal"'})
    return await call_next(request)


def P():
    return get_provider()


def _sym(symbol: str) -> str:
    s = symbol.strip().upper()
    if not s or len(s) > 20:
        raise HTTPException(400, "Invalid symbol")
    return s


def _map(fn, items: list) -> list:
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(fn, items))


# --- market data -----------------------------------------------------------------

@app.get("/api/status")
def status():
    return {"provider": P().name, "demo": settings.demo, "base_currency": settings.base_currency,
            "llm_configured": llm.load_config().configured}


@app.get("/api/market")
def market():
    def tile(sym: str) -> dict:
        h = P().history(sym, "1mo")
        closes = [float(c) for c in h["Close"].tolist()] if not h.empty else []
        last = closes[-1] if closes else None
        prev = closes[-2] if len(closes) > 1 else None
        return {"symbol": sym, "name": MARKET_NAMES.get(sym, sym), "price": last,
                "change_pct": (last / prev - 1) * 100 if last and prev else None, "spark": closes}

    return _map(tile, MARKET)


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=60)):
    return P().search(q)


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    info = P().info(_sym(symbol))
    if info.get("price") is None and info.get("currency") is None:
        raise HTTPException(404, f"No data for {symbol}")
    return info


# Load a longer window for short daily periods so SMA200/RSI are defined from the first visible bar.
WARMUP = {"1mo": ("2y", 31), "3mo": ("2y", 92), "6mo": ("2y", 183), "ytd": ("2y", None), "1y": ("2y", 366)}


@app.get("/api/history/{symbol}")
def history(symbol: str, period: str = "1y"):
    sym = _sym(symbol)
    if period in WARMUP:
        base, days = WARMUP[period]
        rows = analysis.history_payload(P().history(sym, base), intraday=False)
        start = (pd.Timestamp.today().normalize() - pd.Timedelta(days=days) if days
                 else pd.Timestamp(pd.Timestamp.today().year, 1, 1)).strftime("%Y-%m-%d")
        return [r for r in rows if r["time"] >= start]
    return analysis.history_payload(P().history(sym, period), intraday=period in ("1d", "5d"))


@app.get("/api/financials/{symbol}")
def financials(symbol: str, freq: str = "annual"):
    if freq not in ("annual", "quarterly"):
        raise HTTPException(400, "freq must be annual or quarterly")
    return analysis.financials_payload(P().statements(_sym(symbol), freq))


@ttl_cache(3600)
def _valuation(symbol: str) -> dict:
    p = P()
    info = p.info(symbol)
    prices = p.history(symbol, "10y")["Close"]
    long = None if settings.demo else sec.long_history(symbol)
    if long and long["eps"]:
        eps, rps, rep_cur, source = long["eps"], long["revenue_per_share"], long["currency"], long["source"]
    else:
        inc = p.statements(symbol, "annual").get("income")
        eps, rps = {}, {}
        if inc is not None and not inc.empty:
            for col in inc.columns:
                key = col.strftime("%Y-%m-%d")
                e = inc.at["Diluted EPS", col] if "Diluted EPS" in inc.index else None
                if e is not None and e == e:
                    eps[key] = float(e)
                if "Total Revenue" in inc.index and "Diluted Average Shares" in inc.index:
                    r, s = inc.at["Total Revenue", col], inc.at["Diluted Average Shares", col]
                    if r == r and s == s and s:
                        rps[key] = float(r) / float(s)
        rep_cur, source = info.get("financial_currency"), "Yahoo Finance"
    try:
        fx = p.fx(rep_cur or info.get("currency"), info.get("currency"))
    except Exception:
        fx = 1.0
    out = analysis.valuation_history(prices, eps, rps, fx)
    out.update(source=source, current_pe=info.get("pe"), current_ps=info.get("ps"))
    return out


@app.get("/api/valuation/{symbol}")
def valuation(symbol: str):
    return _valuation(_sym(symbol))


@app.get("/api/signals/{symbol}")
def signals(symbol: str):
    s = _sym(symbol)
    p = P()
    try:
        val = _valuation(s)
    except Exception as exc:
        log.warning("valuation(%s) failed: %s", s, exc)
        val = None
    return analysis.signal_check(p.info(s), p.history(s, "2y"), val, p.calendar(s))


@app.get("/api/calendar/{symbol}")
def calendar(symbol: str):
    return P().calendar(_sym(symbol))


@app.get("/api/news")
def news(symbols: str = ""):
    syms = [s for s in (x.strip().upper() for x in symbols.split(",")) if s][:40]
    if not syms:
        syms = list(dict.fromkeys([p["symbol"] for p in db.list_positions()] + [w["symbol"] for w in db.list_watchlist()]))
    if not syms:
        syms = ["^GSPC", "^GDAXI"]
    seen, out = set(), []
    for batch in _map(P().news, syms):
        for n in batch:
            key = n.get("url") or n["title"]
            if key not in seen:
                seen.add(key)
                out.append(n)
    out.sort(key=lambda n: -(n.get("time") or 0))
    return out[:120]


@app.get("/api/news/{symbol}")
def news_symbol(symbol: str):
    return P().news(_sym(symbol))


def _list_row(sym: str) -> dict:
    """Compact row for lists: key figures plus RSI and a quick signal score."""
    p = P()
    info = p.info(sym)
    hist = p.history(sym, "2y")
    quick = analysis.signal_check(info, hist, None, None)
    r = analysis.rsi(hist["Close"]).iloc[-1] if len(hist) > 15 else None
    spark = [float(x) for x in hist["Close"].iloc[-22:].tolist()] if not hist.empty else []
    return {k: info.get(k) for k in ("symbol", "name", "currency", "price", "change", "change_pct", "pe", "forward_pe",
                                     "div_yield", "market_cap", "high52", "low52", "sector", "target_mean")} | {
        "rsi": analysis._clean(r), "score": quick["score"], "light": analysis.light(quick["score"]), "spark": spark}


# --- portfolio --------------------------------------------------------------------

class PositionIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    shares: float = Field(gt=0)
    buy_price: float = Field(ge=0)
    buy_date: str | None = None
    note: str = ""


@app.get("/api/portfolio")
def list_positions():
    return db.list_positions()


@app.post("/api/portfolio")
def add_position(pos: PositionIn):
    pid = db.add_position(pos.symbol, pos.shares, pos.buy_price, pos.buy_date or None, pos.note)
    return {"id": pid}


@app.delete("/api/portfolio/{pid}")
def delete_position(pid: int):
    db.delete_position(pid)
    return {"ok": True}


@app.get("/api/portfolio/summary")
def portfolio_summary():
    s = portfolio.summarize(db.list_positions(), P(), settings.base_currency)
    rows = {r["symbol"]: r for r in _map(_list_row, [p["symbol"] for p in s["positions"]])}
    for pos in s["positions"]:
        extra = rows.get(pos["symbol"], {})
        pos.update(rsi=extra.get("rsi"), score=extra.get("score"), light=extra.get("light"), spark=extra.get("spark"))
    return s


@app.get("/api/portfolio/history")
def portfolio_history(period: str = "1y"):
    return portfolio.value_history(db.list_positions(), P(), settings.base_currency, period)


@app.get("/api/portfolio/export")
def export_positions():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["symbol", "shares", "buy_price", "buy_date", "note"])
    for p in db.list_positions():
        w.writerow([p["symbol"], p["shares"], p["buy_price"], p["buy_date"] or "", p["note"] or ""])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=flower-portfolio.csv"})


@app.post("/api/portfolio/import")
def import_positions(csv_text: str = Body(..., media_type="text/plain")):
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    added, errors = 0, []
    for n, row in enumerate(reader, start=2):
        try:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            pos = PositionIn(symbol=row["symbol"], shares=float(row["shares"].replace(",", ".")),
                             buy_price=float(row["buy_price"].replace(",", ".")),
                             buy_date=row.get("buy_date") or None, note=row.get("note", ""))
            db.add_position(pos.symbol, pos.shares, pos.buy_price, pos.buy_date, pos.note)
            added += 1
        except Exception as exc:
            errors.append(f"line {n}: {exc}")
    return {"added": added, "errors": errors}


# --- watchlist ----------------------------------------------------------------------

class WatchIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    target_price: float | None = None
    note: str = ""


@app.get("/api/watchlist")
def list_watch():
    return db.list_watchlist()


@app.post("/api/watchlist")
def add_watch(w: WatchIn):
    db.upsert_watch(w.symbol, w.target_price, w.note)
    return {"ok": True}


@app.delete("/api/watchlist/{symbol}")
def delete_watch(symbol: str):
    db.delete_watch(_sym(symbol))
    return {"ok": True}


@app.get("/api/watchlist/summary")
def watch_summary():
    items = db.list_watchlist()
    rows = _map(_list_row, [w["symbol"] for w in items])
    for w, r in zip(items, rows):
        r["target_price"] = w["target_price"]
        r["note"] = w["note"]
        price = r.get("price")
        r["to_target_pct"] = (price / w["target_price"] - 1) * 100 if price and w["target_price"] else None
        r["target_hit"] = bool(price and w["target_price"] and price <= w["target_price"])
    return rows


@app.get("/api/overview")
def overview():
    """Everything the Overview page lists: portfolio and watchlist symbols in one list."""
    held = {p["symbol"] for p in db.list_positions()}
    watched = {w["symbol"]: w for w in db.list_watchlist()}
    summary = portfolio.summarize(db.list_positions(), P(), settings.base_currency)
    by_sym = {r["symbol"]: r for r in summary["positions"]}
    syms = list(dict.fromkeys(list(by_sym) + list(watched)))
    rows = _map(_list_row, syms)
    for r in rows:
        pos = by_sym.get(r["symbol"])
        r["in_portfolio"] = r["symbol"] in held
        r["in_watchlist"] = r["symbol"] in watched
        r["value"] = pos["value"] if pos else None
        r["pl"] = pos["pl"] if pos else None
        r["pl_pct"] = pos["pl_pct"] if pos else None
        r["shares"] = pos["shares"] if pos else None
        r["target_price"] = watched[r["symbol"]]["target_price"] if r["symbol"] in watched else None
    return {"summary": {k: v for k, v in summary.items() if k != "positions"}, "rows": rows}


# --- screener -----------------------------------------------------------------------

@app.get("/api/screener/options")
def screener_options():
    return {"presets": {k: v["label"] for k, v in screener.PRESETS.items()}, "regions": screener.REGIONS,
            "sectors": screener.SECTORS}


@app.get("/api/screener")
def run_screener(preset: str | None = None, region: str | None = None, sector: str | None = None,
                 pe_min: float | None = None, pe_max: float | None = None, div_min: float | None = None,
                 roe_min: float | None = None, mcap_min: float | None = None):
    filters = screener.build_filters(preset, {"region": region, "sector": sector, "pe_min": pe_min, "pe_max": pe_max,
                                              "div_min": div_min, "roe_min": roe_min, "mcap_min": mcap_min})
    try:
        return {"filters": filters, "results": P().screen(filters)}
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


# --- settings -------------------------------------------------------------------------

def _hint(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 8 else ("set" if key else "")


@app.get("/api/settings")
def get_settings():
    cfg = llm.load_config()
    return {
        "llm_provider": cfg.provider, "llm_model": cfg.model, "llm_base_url": cfg.base_url,
        "llm_key_hint": _hint(cfg.api_key), "llm_configured": cfg.configured,
        "llm_presets": llm.PRESETS,
        "search_provider": db.get_setting("search_provider", "duckduckgo"),
        "search_key_hint": _hint(db.get_setting("search_api_key", "")),
        "ai_language": db.get_setting("ai_language", "English"),
        "base_currency": settings.base_currency, "demo": settings.demo,
    }


class SettingsIn(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    clear_llm_key: bool = False
    search_provider: str | None = None
    search_api_key: str | None = None
    clear_search_key: bool = False
    ai_language: str | None = None


@app.put("/api/settings")
def put_settings(s: SettingsIn):
    if s.llm_provider is not None:
        if s.llm_provider not in llm.PRESETS:
            raise HTTPException(400, "Unknown LLM provider")
        db.set_setting("llm_provider", s.llm_provider)
    for field in ("llm_model", "llm_base_url", "ai_language"):
        v = getattr(s, field)
        if v is not None:
            db.set_setting(field, v.strip())
    if s.search_provider is not None:
        if s.search_provider not in ("duckduckgo", "tavily", "brave"):
            raise HTTPException(400, "Unknown search provider")
        db.set_setting("search_provider", s.search_provider)
    if s.llm_api_key:
        db.set_setting("llm_api_key", s.llm_api_key.strip())
    if s.clear_llm_key:
        db.set_setting("llm_api_key", "")
    if s.search_api_key:
        db.set_setting("search_api_key", s.search_api_key.strip())
    if s.clear_search_key:
        db.set_setting("search_api_key", "")
    return get_settings()


@app.post("/api/settings/test-llm")
def test_llm():
    try:
        return {"ok": True, "reply": llm.test_connection()}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)


# --- AI -------------------------------------------------------------------------------

def _ndjson(gen: Iterator[dict]) -> StreamingResponse:
    def body():
        try:
            for ev in gen:
                yield json.dumps(ev) + "\n"
        except llm.LLMError as exc:
            yield json.dumps({"type": "error", "text": str(exc)}) + "\n"
        except Exception as exc:
            log.exception("AI stream failed")
            yield json.dumps({"type": "error", "text": str(exc)}) + "\n"

    return StreamingResponse(body(), media_type="application/x-ndjson")


def _ai_inputs(symbol: str) -> tuple[dict, dict]:
    info = P().info(symbol)
    fin = analysis.financials_payload(P().statements(symbol, "annual"))
    return info, fin


@app.get("/api/ai/report/{symbol}")
def cached_report(symbol: str, lens: str = "general"):
    r = db.get_report(_sym(symbol), f"report:{lens}")
    if not r:
        return None
    return {"created": r["created"], "content": r["content"], "sources": json.loads(r["sources"])}


@app.post("/api/ai/report/{symbol}")
def ai_report(symbol: str, lens: str = "general", refresh: bool = False):
    s = _sym(symbol)
    if lens not in reports.LENSES:
        raise HTTPException(400, "Unknown lens")
    info, fin = _ai_inputs(s)
    return _ndjson(reports.stream_report(s, info, fin, lens, refresh))


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[dict] = []


@app.post("/api/ai/ask/{symbol}")
def ai_ask(symbol: str, body: AskIn):
    s = _sym(symbol)
    info, fin = _ai_inputs(s)
    return _ndjson(reports.stream_answer(s, info, fin, body.question, body.history))


# --- frontend -------------------------------------------------------------------------

@app.get("/")
def page_terminal():
    return FileResponse(FRONTEND / "terminal.html")


@app.get("/overview")
def page_overview():
    return FileResponse(FRONTEND / "overview.html")


@app.get("/settings")
def page_settings():
    return FileResponse(FRONTEND / "settings.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
