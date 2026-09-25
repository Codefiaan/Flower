"""End-to-end tests in a real browser (Chromium via Playwright), demo data.

Skipped automatically when Playwright isn't installed. Any JavaScript error or console
error on a page fails the test.
"""
import glob
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pw = pytest.importorskip("playwright.sync_api")

AI_TEXT = "## Business in brief\nSolid company [S1].\n\n## Main risks\n- Competition\n"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StubLLM(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(AI_TEXT), 10):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': AI_TEXT[i:i + 10]}}]})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn

    from backend import cache, db

    db.set_path(tmp_path_factory.mktemp("e2e") / "e2e.db")
    db.set_setting("pref:data_mode", '"demo"')
    cache.clear()
    llm_srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubLLM)
    threading.Thread(target=llm_srv.serve_forever, daemon=True).start()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config("backend.main:app", host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    yield {"base": base, "llm": f"http://127.0.0.1:{llm_srv.server_address[1]}/v1"}
    srv.should_exit = True
    llm_srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            exe = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [None])[0]
            if not exe:
                pytest.skip("no Chromium available for Playwright")
            b = p.chromium.launch(executable_path=exe)
        yield b
        b.close()


class Page:
    """A page that records JS errors; `allow` lists console messages expected by the test."""

    def __init__(self, browser, base, width=1440, height=900, **ctx):
        self.ctx = browser.new_context(viewport={"width": width, "height": height}, **ctx)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(10_000)
        self.base, self.errors, self.allow = base, [], []
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(f"console: {m.text}"))
        self.page.on("dialog", lambda d: d.accept())

    def goto(self, path):
        self.page.goto(self.base + path)
        return self.page

    def close(self):
        real = [e for e in self.errors if not any(a in e for a in self.allow)]
        self.ctx.close()
        assert not real, "JavaScript errors:\n" + "\n".join(real)


@pytest.fixture()
def page(browser, server):
    p = Page(browser, server["base"])
    yield p
    p.close()


def api(page, method, path, data=None):
    r = page.page.request.fetch(page.base + path, method=method, data=data)
    assert r.ok, f"{method} {path} -> {r.status} {r.text()}"
    return r.json()


# --- Terminal --------------------------------------------------------------------------

def test_terminal_function_keys_and_command_line(page):
    pg = page.goto("/terminal")
    pg.wait_for_selector(".tile")
    assert pg.locator(".tile").count() >= 5
    for key, crumb in (("F2", "PORT"), ("F3", "WATCH"), ("F4", "SCRN"), ("F5", "NEWS"), ("F9", "HELP"), ("F1", "MKT")):
        pg.keyboard.press(key)
        pg.wait_for_function(f"document.querySelector('#crumb').textContent === '{crumb}'")
    pg.fill("#cmd", "sie")
    pg.wait_for_selector("#suggest div[data-i]")
    pg.keyboard.press("ArrowDown")
    pg.keyboard.press("Enter")
    pg.wait_for_function("location.hash === '#/sec/SIE.DE/des'")
    pg.wait_for_selector("#sig .sig tr")
    for tab, marker in (("gp", "#pc canvas"), ("fa", "#fa table"), ("ev", "#evt table"), ("cn", "#cn .news-item"), ("ai", "#gen")):
        pg.click(f".tabs button[data-t={tab}]")
        pg.wait_for_selector(marker)
    pg.fill("#cmd", "AAPL FA")
    pg.keyboard.press("Enter")
    pg.wait_for_function("location.hash === '#/sec/AAPL/fa'")
    pg.wait_for_selector("#fa table")


def test_terminal_portfolio_add_import_delete(page):
    pg = page.goto("/terminal#/portfolio")
    pg.fill("#add-pos input[name=symbol]", "msft")
    pg.fill("#add-pos input[name=shares]", "3")
    pg.fill("#add-pos input[name=buy_price]", "300")
    pg.fill("#add-pos input[name=buy_date]", "2025-02-03")
    pg.click("#add-pos button")
    pg.wait_for_selector("#pf-table tr[data-s=MSFT]")
    pg.click("#imp-toggle")
    pg.fill("#imp textarea", "symbol,shares,buy_price,buy_date,note\nKO,10,55,2024-05-01,div")
    pg.click("#imp button")
    pg.wait_for_selector("#pf-table tr[data-s=KO]")
    assert "IMPORTED 1" in pg.text_content("#status")
    pg.wait_for_selector("#pf-alloc .bar")
    pg.click("button[data-lots=KO]")
    pg.click("tr[data-sub=KO] button[data-del]")
    pg.wait_for_function("!document.querySelector('#pf-table tr[data-s=KO]')")
    lots = api(page, "GET", "/api/portfolio")
    assert [l["symbol"] for l in lots].count("KO") == 0


def test_terminal_screener_to_watchlist(page):
    pg = page.goto("/terminal#/screener")
    pg.click("#presets button[data-p=value]")
    pg.wait_for_selector("#scr-out tr[data-s]")
    sym = pg.get_attribute("#scr-out tr[data-s]", "data-s")
    pg.click(f"#scr-out button[data-wl='{sym}']")
    pg.wait_for_function(f"document.querySelector(\"#scr-out button[data-wl='{sym}']\").disabled")
    pg.keyboard.press("F3")
    pg.wait_for_selector(f"#wl tr[data-s='{sym}']")
    pg.click(f"#wl button[data-rm='{sym}']")
    pg.wait_for_function(f"!document.querySelector(\"#wl tr[data-s='{sym}']\")")


def test_terminal_ai_report_and_chat(page, server):
    api(page, "PUT", "/api/settings", {"llm_provider": "custom", "llm_api_key": "sk-e2e-00000000",
                                       "llm_model": "stub", "llm_base_url": server["llm"]})
    pg = page.goto("/terminal#/sec/AAPL/ai")
    pg.click("#regen")
    pg.wait_for_selector("#ai-out h3")
    assert "BUSINESS IN BRIEF" in pg.text_content("#ai-out").upper()
    pg.fill("#ask input", "What are the risks?")
    pg.keyboard.press("Enter")
    pg.wait_for_function("document.querySelector('#chat').textContent.includes('Competition')")


# --- Overview ------------------------------------------------------------------------

def test_overview_list_sheet_add_and_theme(page):
    api(page, "POST", "/api/portfolio", {"symbol": "SAP.DE", "shares": 5, "buy_price": 150})
    api(page, "POST", "/api/watchlist", {"symbol": "NVDA", "target_price": 1})
    pg = page.goto("/overview")
    pg.wait_for_selector(".row-item[data-s='SAP.DE']")
    assert pg.text_content("#pv").startswith("€")
    pg.click("#filter button[data-f=watchlist]")
    assert pg.locator(".row-item[data-s='SAP.DE']").count() == 0
    pg.click("#filter button[data-f=all]")
    pg.fill("#q", "nvidia")
    pg.wait_for_function("document.querySelectorAll('.row-item').length === 1")
    pg.fill("#q", "")
    pg.select_option("#sort", "name")
    pg.click(".row-item[data-s='SAP.DE']")
    pg.wait_for_selector("#sSig .check")
    pg.wait_for_selector("#sFacts .fact")
    pg.keyboard.press("Escape")
    assert pg.is_hidden("#sheet")
    pg.click("#add")
    pg.fill("#addSearch", "basf")
    pg.click("#addResults button")
    pg.fill("#addForm input[name=shares]", "4")
    pg.fill("#addForm input[name=buy_price]", "40")
    pg.click("#addOk")
    pg.wait_for_selector(".row-item[data-s='BAS.DE']")
    before = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()")
    pg.click("#theme")
    pg.wait_for_function(f"getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() !== '{before}'")
    assert api(page, "GET", "/api/settings")["theme"] in ("light", "dark")
    pg.click(".pages a[href='/terminal']")
    pg.wait_for_url("**/terminal")


# --- Settings --------------------------------------------------------------------------

def test_settings_sections_save_and_validate(page):
    page.allow.append("400 (Bad Request)")
    pg = page.goto("/settings")
    pg.wait_for_selector("#base_currency option", state="attached")
    pg.click("[data-name=start_page] button[data-v=overview]")
    pg.select_option("#base_currency", "USD")
    pg.select_option("#refresh_minutes", "5")
    pg.click("#general .primary")
    pg.wait_for_function("document.querySelector('#toast').textContent === 'Saved'")
    s = api(page, "GET", "/api/settings")
    assert (s["start_page"], s["base_currency"], s["refresh_minutes"]) == ("overview", "USD", 5)

    pg.fill("#market_symbols", "^GSPC, not valid!")
    pg.click("#data .primary")
    pg.wait_for_function("document.querySelector('#toast').classList.contains('bad')")
    assert "Invalid symbols" in pg.text_content("#toast")
    pg.click("#resetMarket")
    pg.click("#data .primary")
    pg.wait_for_function("!document.querySelector('#toast').classList.contains('bad')")

    pg.select_option("#llm_provider", "deepseek")
    assert pg.input_value("#llm_base_url") == "https://api.deepseek.com"
    pg.fill("#llm_api_key", "sk-settings-test-9876")
    pg.click("#ai .primary")
    pg.wait_for_function("document.querySelector('#keyHint').textContent.includes('9876')")
    assert pg.input_value("#llm_api_key") == ""  # never shown again
    assert "sk-settings-test" not in json.dumps(api(page, "GET", "/api/settings"))

    pg.click("[data-name=search_provider] button[data-v=tavily]")
    assert pg.is_visible("#search_api_key")
    pg.click("#search .primary")
    pg.wait_for_function("document.querySelector('#toast').textContent === 'Saved'")
    assert api(page, "GET", "/api/settings")["search_provider"] == "tavily"
    api(page, "PUT", "/api/settings", {"start_page": "terminal", "base_currency": "EUR", "refresh_minutes": 0,
                                       "search_provider": "duckduckgo"})


def test_settings_login_roundtrip(browser, server):
    # credentials are only sent once the server asks for them, i.e. after the login is saved
    p = Page(browser, server["base"], http_credentials={"username": "eric", "password": "correct horse"})
    pg = p.goto("/settings")
    pg.wait_for_selector("#loginFields")
    pg.fill("#lu", "eric")
    pg.fill("#lp", "correct horse")
    pg.fill("#lp2", "different")
    pg.click("#saveLogin")
    assert "don't match" in pg.text_content("#loginResult")
    pg.fill("#lp2", "correct horse")
    pg.click("#saveLogin")
    pg.wait_for_function("document.querySelector('#loginResult').textContent.includes('Login saved')")
    p.close()
    anon = browser.new_context()
    assert anon.request.get(server["base"] + "/api/status").status == 401
    anon.close()
    authed = browser.new_context(http_credentials={"username": "eric", "password": "correct horse"})
    assert authed.request.get(server["base"] + "/api/status").status == 200
    assert authed.request.delete(server["base"] + "/api/settings/login").ok
    authed.close()


# --- Navigation & phone layout ---------------------------------------------------------

def test_page_switcher_everywhere(page):
    pg = page.goto("/terminal")
    for target, url in (("OVERVIEW", "/overview"), ("Settings", "/settings"), ("Terminal", "/terminal")):
        pg.click(f".pages a:text-is('{target}')")
        pg.wait_for_url("**" + url)
        assert pg.get_attribute(".pages a.active", "href") == url


@pytest.mark.parametrize("path", ["/terminal", "/overview", "/settings", "/terminal#/portfolio", "/terminal#/sec/AAPL/des"])
def test_phone_width_has_no_horizontal_scroll(browser, server, path):
    p = Page(browser, server["base"], width=390, height=844)
    pg = p.goto(path)
    pg.wait_for_load_state("networkidle")
    overflow = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    p.close()
    assert overflow <= 1, f"{path} scrolls sideways by {overflow}px on a phone"


def test_rapid_tab_switching_has_no_errors(page):
    """Charts must survive being removed while their data is still loading (seen on fast CI machines)."""
    pg = page.goto("/terminal#/sec/AAPL/des")
    pg.wait_for_selector(".tabs button[data-t=gp]")
    # slow down the price-history responses so they always arrive after the chart was removed
    pg.route("**/api/history/**", lambda route: (time.sleep(0.4), route.continue_())[1])
    for _ in range(3):
        for tab in ("gp", "des", "ev", "gp", "fa", "des"):
            pg.click(f".tabs button[data-t={tab}]", no_wait_after=True)
    pg.click(".pages a[href='/overview']")
    pg.wait_for_selector(".row-item, .empty")
    pg.wait_for_timeout(2500)  # let the delayed responses land


def test_overview_rapid_sheet_switching(page):
    api(page, "POST", "/api/portfolio", {"symbol": "KO", "shares": 1, "buy_price": 50})
    api(page, "POST", "/api/watchlist", {"symbol": "JNJ"})
    pg = page.goto("/overview")
    pg.wait_for_selector(".row-item[data-s=KO]")
    pg.route("**/api/history/**", lambda route: (time.sleep(0.4), route.continue_())[1])
    for _ in range(3):
        for sym in ("KO", "JNJ"):
            pg.click(f".row-item[data-s={sym}]", no_wait_after=True)
            pg.click("#sPeriods button[data-p='1mo']", no_wait_after=True)
        pg.keyboard.press("Escape")
        pg.click("#periods button[data-p='1mo']", no_wait_after=True)
    pg.wait_for_timeout(2500)
