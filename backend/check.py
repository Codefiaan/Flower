"""Health check for a Flower installation.

    python -m backend.check                 environment + in-process app self-test (demo data)
    python -m backend.check --live          + real Yahoo Finance / SEC EDGAR data
    python -m backend.check --llm           + a test call to the AI model saved in Settings
    python -m backend.check --report FILE   also write everything to FILE (send it for support)

Exit code 0 = no FAIL (WARN is allowed), 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import importlib
import math
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable

RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)
REQUIRED = ["fastapi", "uvicorn", "yfinance", "pandas", "numpy", "httpx", "pypdf", "pydantic", "starlette"]


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    colour = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m"}.get(status, "")
    reset = "\033[0m" if colour and sys.stdout.isatty() else ""
    colour = colour if reset else ""
    print(f"{colour}{status:4}{reset}  {name}" + (f"  -  {detail}" if detail else ""), flush=True)


def check(name: str, fn: Callable[[], str | None], warn_only: bool = False) -> None:
    """Run one check. `fn` returns a detail string (PASS) or raises (FAIL/WARN)."""
    try:
        detail = fn()
        record("PASS", name, detail or "")
    except Warn as w:
        record("WARN", name, str(w))
    except Exception as exc:  # noqa: BLE001 - report every kind of failure
        msg = f"{type(exc).__name__}: {exc}"
        record("WARN" if warn_only else "FAIL", name, msg)
        if os.environ.get("FLOWER_CHECK_DEBUG"):
            traceback.print_exc()


class Warn(Exception):
    pass


def ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def good_number(v) -> bool:
    return isinstance(v, (int, float)) and not math.isnan(v) and v > 0


# --- environment ------------------------------------------------------------------

def environment_checks() -> None:
    def py():
        ok(sys.version_info >= (3, 10), f"Python {platform.python_version()} is too old, 3.10+ required")
        return f"Python {platform.python_version()} ({sys.executable})"

    def venv():
        if sys.prefix == sys.base_prefix:
            raise Warn("not running inside a virtual environment (.venv); start.bat/start.sh create one")
        return sys.prefix

    def packages():
        missing, versions = [], []
        for p in REQUIRED:
            try:
                m = importlib.import_module(p)
                versions.append(f"{p} {getattr(m, '__version__', '?')}")
            except ImportError:
                missing.append(p)
        ok(not missing, f"missing: {', '.join(missing)} - run: pip install -r requirements.txt")
        return ", ".join(versions)

    def database():
        from . import db

        db.init()
        path = db.current_path()
        db.set_setting("check:last_run", time.strftime("%Y-%m-%d %H:%M:%S"))
        return str(path)

    def sec_contact():
        from . import prefs

        contact = prefs.get("sec_contact")
        if contact == prefs.PLACEHOLDER_SEC_CONTACT:
            raise Warn("works, but the SEC asks for your real address: Settings -> Data & markets -> "
                       "Contact e-mail for SEC EDGAR")
        return contact

    def settings_summary():
        from . import llm, prefs

        cfg = llm.load_config()
        login = prefs.login_source() or "none"
        return (f"data={prefs.get('data_mode')}, currency={prefs.get('base_currency')}, "
                f"AI={'configured (' + cfg.provider + ', ' + cfg.model + ')' if cfg.configured else 'not configured'}, "
                f"login={login}")

    check("Python version", py)
    check("Virtual environment", venv)
    check("Required packages", packages)
    check("Database writable", database)
    check("Settings", settings_summary, warn_only=True)
    check("SEC contact address", sec_contact, warn_only=True)


# --- in-process app self-test (demo data, temporary database) ---------------------------

def app_selftest() -> None:
    from fastapi.testclient import TestClient

    from . import cache, db
    from .main import app

    real_path = db.current_path()
    tmp = Path(tempfile.mkdtemp(prefix="flower-check-")) / "selftest.db"
    db.set_path(tmp)
    db.set_setting("pref:data_mode", '"demo"')
    cache.clear()
    client = TestClient(app)
    try:
        def pages():
            for url in ("/terminal", "/overview", "/settings", "/static/terminal.js", "/static/vendor/lightweight-charts.js"):
                r = client.get(url)
                ok(r.status_code == 200, f"{url} -> HTTP {r.status_code}")
            return "terminal, overview, settings, static files"

        def api():
            client.post("/api/portfolio", json={"symbol": "AAPL", "shares": 2, "buy_price": 100})
            client.post("/api/watchlist", json={"symbol": "SAP.DE", "target_price": 1})
            urls = ["/api/status", "/api/market", "/api/quote/AAPL", "/api/history/AAPL?period=1y",
                    "/api/financials/AAPL", "/api/valuation/AAPL", "/api/signals/AAPL", "/api/news",
                    "/api/screener?preset=value", "/api/portfolio/summary", "/api/portfolio/history",
                    "/api/watchlist/summary", "/api/overview", "/api/settings", "/api/search?q=sap"]
            for url in urls:
                r = client.get(url)
                ok(r.status_code == 200, f"{url} -> HTTP {r.status_code}: {r.text[:200]}")
            return f"{len(urls)} API routes OK"

        check("App pages", pages)
        check("App API (demo data)", api)
    finally:
        db.set_path(real_path)
        cache.clear()


# --- live data ---------------------------------------------------------------------

def _sec_optional() -> bool:
    """CI sets FLOWER_CHECK_SEC_OPTIONAL=1 because the SEC may block shared cloud servers;
    on a normal PC SEC problems stay real failures."""
    return os.environ.get("FLOWER_CHECK_SEC_OPTIONAL") == "1"


def network_checks() -> bool:
    """Can this machine reach the data sources at all? Returns False if Yahoo is unreachable."""
    import httpx

    reachable = {}

    def probe(name: str, url: str):
        def fn():
            try:
                r = httpx.get(url, timeout=15, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0 Flower-check (flower-terminal@example.com)"})
            except httpx.HTTPError as exc:
                reachable[name] = False
                raise AssertionError(f"cannot connect ({type(exc).__name__}: {exc}) - firewall, proxy or no internet?")
            reachable[name] = r.status_code < 500
            if r.status_code in (401, 403, 429):
                raise Warn(f"HTTP {r.status_code} - the service is reachable but refused this request "
                           "(rate limit or blocked network)")
            return f"HTTP {r.status_code}"
        return fn

    check("Network: Yahoo Finance", probe("yahoo", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d"))
    def sec_probe():
        # Same client and User-Agent as the app, so this tests what the app will really get.
        from .providers import sec

        try:
            data = sec._get_json("https://www.sec.gov/files/company_tickers.json")
        except sec.SECError as exc:
            raise AssertionError(str(exc))
        except httpx.HTTPError as exc:
            raise AssertionError(f"cannot connect ({type(exc).__name__}: {exc}) - firewall, proxy or no internet?")
        return f"OK, {len(data)} companies in EDGAR's ticker list (User-Agent: {sec.user_agent()})"

    check("Network: SEC EDGAR", sec_probe, warn_only=_sec_optional())
    return reachable.get("yahoo", False)


def live_checks() -> None:
    from . import cache
    from .providers import sec
    from .providers.yahoo import YahooProvider

    cache.clear()
    y = YahooProvider()

    def quote(sym: str, needs_fundamentals: bool):
        def fn():
            i = y.info(sym)
            ok(good_number(i.get("price")), f"no price for {sym} (Yahoo returned no usable quote)")
            ok(i.get("currency"), f"no currency for {sym}")
            if needs_fundamentals:
                missing = [k for k in ("pe", "market_cap", "high52", "low52", "sector") if i.get(k) is None]
                if len(missing) > 2:
                    raise Warn(f"price {i['price']:.2f} {i['currency']} but missing fields: {', '.join(missing)}")
            return f"{i['name']}: {i['price']:.2f} {i['currency']}, P/E {i.get('pe')}"
        return fn

    def history(sym: str):
        def fn():
            h = y.history(sym, "1y")
            ok(len(h) > 150, f"only {len(h)} daily rows for {sym}")
            ok(good_number(float(h['Close'].iloc[-1])), "last close is not a positive number")
            intraday = y.history(sym, "1d")
            if intraday.empty:
                raise Warn(f"{len(h)} daily rows, but no intraday data (market may be closed)")
            return f"{len(h)} daily rows, {len(intraday)} intraday rows"
        return fn

    def statements(sym: str):
        def fn():
            st = y.statements(sym, "annual")
            for key in ("income", "balance", "cashflow"):
                ok(not st[key].empty, f"{key} statement empty")
            inc = st["income"]
            for row in ("Total Revenue", "Net Income", "Diluted EPS"):
                ok(row in inc.index, f"income statement has no '{row}' row (rows: {', '.join(map(str, inc.index[:8]))}...)")
            q = y.statements(sym, "quarterly")
            return f"{inc.shape[1]} annual periods, {q['income'].shape[1]} quarterly periods"
        return fn

    def news(sym: str):
        def fn():
            n = y.news(sym)
            ok(n, "no news items")
            ok(all(i.get("title") and i.get("url") for i in n), "news items without title or url")
            ok(any(i.get("time") for i in n), "news items without timestamps")
            return f"{len(n)} items, latest: {n[0]['title'][:60]}"
        return fn

    def calendar():
        c = y.calendar("AAPL")
        if not c.get("earnings_date"):
            raise Warn(f"no earnings date in calendar: {c}")
        return str(c)

    def search():
        r = y.search("Siemens")
        ok(any(x["symbol"] == "SIE.DE" for x in r), f"SIE.DE not found, got {[x['symbol'] for x in r]}")
        return f"{len(r)} results"

    def screener():
        from .screener import build_filters

        res = y.screen(build_filters("value", {"region": "de"}))
        ok(res, "no results for 'value' preset in Germany")
        ok(all(r["symbol"] for r in res), "results without symbol")
        with_pe = [r for r in res if r.get("pe") is not None]
        ok(with_pe, "no P/E values in screener results")
        bad = [r["symbol"] for r in with_pe if not (0 < r["pe"] <= 15 * 1.05 + 0.01)]
        ok(not bad, f"P/E filter not applied: {bad[:5]}")
        return f"{len(res)} results, e.g. {res[0]['symbol']} P/E {res[0]['pe']}"

    def fx():
        r = y.fx("USD", "EUR")
        ok(0.5 < r < 1.5, f"implausible USD->EUR rate {r}")
        gbp = y.fx("GBp", "EUR")
        ok(0.005 < gbp < 0.02, f"implausible GBp->EUR rate {gbp}")
        return f"USD->EUR {r:.4f}, GBp->EUR {gbp:.5f}"

    def sec_history():
        sec.company_facts(sec.cik_for_or_raise("AAPL"))  # surfaces the SEC's exact answer on failure
        h = sec.long_history("AAPL")
        ok(h and len(h["eps"]) >= 8, f"expected 8+ years of EPS from SEC, got {h and len(h['eps'])}")
        years = sorted(h["eps"])
        return f"{len(years)} fiscal years of EPS ({years[0][:4]}-{years[-1][:4]}), currency {h['currency']}"

    def sec_filings():
        cik = sec.cik_for_or_raise("AAPL")
        ok(cik == 320193, f"unexpected CIK for AAPL: {cik}")
        latest = sec.latest_filings(cik)
        annual, quarterly = latest.get("annual"), latest.get("quarterly")
        ok(annual is not None, "no annual report (10-K) found in EDGAR's filing list")
        from datetime import date

        age_days = (date.today() - date.fromisoformat(annual["filed"])).days
        ok(age_days <= 460, f"newest annual report is {age_days} days old ({annual['form']} filed {annual['filed']})")
        found = [x for x in (annual, quarterly) if x]
        return ", ".join(f"{x['form']} filed {x['filed']} (period {x['period']})" for x in found)

    for sym, fundamentals in (("AAPL", True), ("SAP.DE", True), ("^GDAXI", False), ("EURUSD=X", False)):
        check(f"Yahoo quote {sym}", quote(sym, fundamentals))
    check("Yahoo history AAPL", history("AAPL"))
    check("Yahoo statements AAPL", statements("AAPL"))
    check("Yahoo statements SAP.DE", statements("SAP.DE"))
    check("Yahoo news AAPL", news("AAPL"), warn_only=True)
    check("Yahoo calendar AAPL", calendar, warn_only=True)
    check("Yahoo search", search)
    check("Yahoo screener", screener)
    check("FX rates", fx)
    check("SEC EDGAR XBRL history", sec_history, warn_only=_sec_optional())
    check("SEC EDGAR filings", sec_filings, warn_only=_sec_optional())


def llm_check() -> None:
    from . import llm

    def fn():
        cfg = llm.load_config()
        if not cfg.configured:
            raise Warn("no AI model configured (Settings -> AI model & API key)")
        reply = llm.test_connection(cfg)
        return f"{cfg.provider} / {cfg.model} replied: {reply!r}"

    check("AI model connection", fn)


# --- main --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a Flower installation")
    ap.add_argument("--live", action="store_true", help="also test real Yahoo Finance and SEC EDGAR data")
    ap.add_argument("--llm", action="store_true", help="also test the AI model saved in Settings")
    ap.add_argument("--report", metavar="FILE", help="write the results to FILE")
    args = ap.parse_args(argv)

    import logging

    # Windows consoles often use a legacy code page; never crash on a headline with special characters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    started = time.time()
    print(f"Flower check on {platform.platform()}\n")
    environment_checks()
    if not any(s == "FAIL" for s, *_ in RESULTS):
        try:
            app_selftest()
        except Exception as exc:  # noqa: BLE001
            record("FAIL", "App self-test", f"{type(exc).__name__}: {exc}")
    if args.live:
        if network_checks():
            live_checks()
        else:
            record("FAIL", "Live data", "skipped - Yahoo Finance is not reachable from this machine (see network check)")
    if args.llm:
        llm_check()

    fails = sum(1 for s, *_ in RESULTS if s == "FAIL")
    warns = sum(1 for s, *_ in RESULTS if s == "WARN")
    summary = f"\n{len(RESULTS)} checks: {fails} failed, {warns} warnings ({time.time() - started:.0f}s)"
    print(summary)
    if args.report:
        lines = [f"Flower check report {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"Platform: {platform.platform()} | Python {platform.python_version()} | {sys.executable}",
                 f"Options: live={args.live} llm={args.llm}", ""]
        lines += [f"{s:4}  {n}" + (f"  -  {d}" if d else "") for s, n, d in RESULTS]
        lines.append(summary.strip())
        Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Report written to {Path(args.report).resolve()}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
