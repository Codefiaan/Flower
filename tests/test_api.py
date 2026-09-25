import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def test_market_data_routes(client):
    for url in ["/api/status", "/api/market", "/api/quote/AAPL", "/api/history/SAP.DE?period=1y",
                "/api/history/AAPL?period=1d", "/api/history/AAPL?period=max", "/api/financials/SIE.DE",
                "/api/financials/SIE.DE?freq=quarterly", "/api/valuation/AAPL", "/api/signals/ALV.DE",
                "/api/news/KO", "/api/calendar/KO", "/api/search?q=sie", "/api/screener/options",
                "/api/screener?preset=value", "/api/screener?preset=dividend&region=de", "/api/news"]:
        r = client.get(url)
        assert r.status_code == 200, url


def test_history_has_sma200_from_first_bar(client):
    rows = client.get("/api/history/AAPL?period=1y").json()
    assert rows and rows[0]["sma200"] is not None and rows[0]["rsi"] is not None


def test_bad_inputs(client):
    assert client.get("/api/financials/AAPL?freq=weekly").status_code == 400
    assert client.post("/api/portfolio", json={"symbol": "AAPL", "shares": -1, "buy_price": 1}).status_code == 422
    assert client.post("/api/ai/report/AAPL?lens=nonsense").status_code == 400


def test_portfolio_crud_and_csv(client):
    r = client.post("/api/portfolio", json={"symbol": "aapl", "shares": 10, "buy_price": 150, "buy_date": "2025-01-15"})
    pid = r.json()["id"]
    client.post("/api/portfolio", json={"symbol": "SHEL.L", "shares": 100, "buy_price": 2400})
    s = client.get("/api/portfolio/summary").json()
    assert {p["symbol"] for p in s["positions"]} == {"AAPL", "SHEL.L"}
    assert s["total_value"] > 0 and s["base_currency"] == "EUR"
    assert len(client.get("/api/portfolio/history").json()) > 100

    csv_text = client.get("/api/portfolio/export").text
    assert csv_text.startswith("symbol,shares,buy_price")
    r = client.post("/api/portfolio/import", content="symbol,shares,buy_price,buy_date,note\nKO,5,\"55,5\",2024-01-02,div\nBAD,x,1,,\n",
                    headers={"Content-Type": "text/plain"})
    assert r.json()["added"] == 1 and len(r.json()["errors"]) == 1

    client.delete(f"/api/portfolio/{pid}")
    assert "AAPL" not in {p["symbol"] for p in client.get("/api/portfolio").json()}


def test_watchlist_and_overview(client):
    client.post("/api/watchlist", json={"symbol": "SAP.DE", "target_price": 10_000})
    client.post("/api/watchlist", json={"symbol": "SAP.DE", "target_price": 1})  # upsert
    rows = client.get("/api/watchlist/summary").json()
    assert len(rows) == 1 and rows[0]["target_price"] == 1 and rows[0]["target_hit"] is False
    client.post("/api/portfolio", json={"symbol": "MSFT", "shares": 1, "buy_price": 100})
    ov = client.get("/api/overview").json()
    by = {r["symbol"]: r for r in ov["rows"]}
    assert by["MSFT"]["in_portfolio"] and by["SAP.DE"]["in_watchlist"]
    client.delete("/api/watchlist/SAP.DE")
    assert client.get("/api/watchlist").json() == []


def test_settings_never_return_keys(client):
    r = client.put("/api/settings", json={"llm_provider": "deepseek", "llm_api_key": "sk-secret-123456"}).json()
    assert r["llm_key_hint"] == "...3456"
    assert "sk-secret" not in json.dumps(r)
    assert client.put("/api/settings", json={"llm_provider": "nope"}).status_code == 400
    r = client.put("/api/settings", json={"clear_llm_key": True}).json()
    assert r["llm_key_hint"] == "" and r["llm_configured"] is False


def test_ai_report_without_key_reports_error(client):
    events = [json.loads(line) for line in client.post("/api/ai/report/AAPL").text.splitlines()]
    assert events[-1]["type"] == "error" and "Settings" in events[-1]["text"]


class _StubLLM(BaseHTTPRequestHandler):
    """Streams a canned answer in OpenAI or Anthropic SSE format."""
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _StubLLM.requests.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for word in ["## Business", " in brief\n", "Solid [S1]."]:
            if self.path.endswith("/v1/messages"):
                ev = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": word}}
            else:
                ev = {"choices": [{"delta": {"content": word}}]}
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
        if not self.path.endswith("/v1/messages"):
            self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


@pytest.fixture()
def stub_llm():
    srv = HTTPServer(("127.0.0.1", 0), _StubLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _StubLLM.requests = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.parametrize("provider,base_suffix", [("custom", "/v1"), ("anthropic", "")])
def test_ai_report_streams_and_caches(client, stub_llm, provider, base_suffix):
    client.put("/api/settings", json={"llm_provider": provider, "llm_api_key": "test-key-000000",
                                      "llm_model": "stub-model", "llm_base_url": stub_llm + base_suffix})
    events = [json.loads(l) for l in client.post("/api/ai/report/AAPL?lens=value").text.splitlines()]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "## Business in brief\nSolid [S1]."
    assert events[-1]["type"] == "done"
    path, headers, body = _StubLLM.requests[-1]
    if provider == "anthropic":
        assert path == "/v1/messages" and headers["x-api-key"] == "test-key-000000"
        assert "Graham" in body["system"]
    else:
        assert path == "/v1/chat/completions" and headers["Authorization"] == "Bearer test-key-000000"
        assert "Graham" in body["messages"][0]["content"]
    assert "key_figures" in json.dumps(body)
    # second call is served from the cache without hitting the LLM
    n = len(_StubLLM.requests)
    cached = client.get("/api/ai/report/AAPL?lens=value").json()
    assert cached["content"] == text
    client.post("/api/ai/report/AAPL?lens=value")
    assert len(_StubLLM.requests) == n
    # chat
    events = [json.loads(l) for l in client.post("/api/ai/ask/AAPL", json={"question": "Risks?", "history": []}).text.splitlines()]
    assert any(e["type"] == "delta" for e in events)
    assert client.post("/api/settings/test-llm").json()["ok"] is True


def test_basic_auth(client, monkeypatch):
    from backend import main
    from backend.config import Settings

    monkeypatch.setattr(main, "settings", Settings(user="me", password="pw"))
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", auth=("me", "wrong")).status_code == 401
    assert client.get("/api/status", auth=("me", "pw")).status_code == 200


def test_proxy_without_login_is_refused(client):
    assert client.get("/api/status", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403


def test_pages_served(client):
    for url, marker in [("/", "FLOWER"), ("/overview", "flower"), ("/settings", "Settings"),
                        ("/static/vendor/lightweight-charts.js", "LightweightCharts")]:
        r = client.get(url)
        assert r.status_code == 200 and marker in r.text
