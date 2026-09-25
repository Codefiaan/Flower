"""Search, SEC EDGAR, document download and LLM error handling against a fake HTTP layer."""
import json

import httpx
import pytest

from backend import cache, db, llm, prefs, reports
from backend.providers import sec


def make_pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return pdf + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


LONG = "Outlook and guidance: revenue growth expected, operating margin stable, key risks include competition. " * 80


@pytest.fixture()
def net(monkeypatch, tmp_path):
    """Route every httpx request to `routes` (url substring -> response factory) and record requests."""
    db.set_path(tmp_path / "net.db")
    prefs.update({"data_mode": "live"})
    cache.clear()
    routes, seen = {}, []

    def handle(self, request):
        seen.append(request)
        for key, factory in routes.items():
            if key in str(request.url):
                return factory(request)
        return httpx.Response(404, text="not mocked: " + str(request.url))

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    yield routes, seen
    cache.clear()


def test_duckduckgo_parsing_and_ranking(net):
    routes, seen = net
    html = """<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.siemens.com%2Freport%2Fannual-report-2025.pdf&amp;rut=x">Siemens <b>Annual Report</b> 2025</a>
              <a class="result__a" href="https://en.wikipedia.org/wiki/Siemens">Siemens - Wikipedia</a>"""
    routes["duckduckgo.com/html"] = lambda r: httpx.Response(200, text=html)
    res = reports.web_search("Siemens annual report")
    assert res[0] == {"title": "Siemens Annual Report 2025", "url": "https://www.siemens.com/report/annual-report-2025.pdf"}
    assert reports.rank_results(res, "https://www.siemens.com")[0]["url"].endswith(".pdf")
    assert seen[0].method == "POST" and b"q=Siemens" in seen[0].content


@pytest.mark.parametrize("provider", ["tavily", "brave"])
def test_keyed_search_providers(net, provider):
    routes, seen = net
    db.set_setting("search_provider", provider)
    db.set_setting("search_api_key", "key-123")
    routes["api.tavily.com"] = lambda r: httpx.Response(200, json={"results": [{"title": "T", "url": "https://t/a.pdf"}]})
    routes["api.search.brave.com"] = lambda r: httpx.Response(200, json={"web": {"results": [{"title": "B", "url": "https://b/a.pdf"}]}})
    res = reports.web_search("x")
    assert len(res) == 1 and res[0]["url"].endswith("a.pdf")
    req = seen[-1]
    if provider == "tavily":
        assert req.headers["Authorization"] == "Bearer key-123"
    else:
        assert req.headers["X-Subscription-Token"] == "key-123"


def test_search_key_missing_falls_back_to_duckduckgo(net):
    routes, seen = net
    db.set_setting("search_provider", "tavily")
    routes["duckduckgo.com/html"] = lambda r: httpx.Response(200, text="")
    assert reports.web_search("x") == []
    assert "duckduckgo" in str(seen[0].url)


def _sec_routes(routes):
    routes["company_tickers.json"] = lambda r: httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
    routes["submissions/CIK0000320193.json"] = lambda r: httpx.Response(200, json={"name": "Apple Inc.", "filings": {"recent": {
        "form": ["8-K", "10-Q", "10-K", "10-Q"], "accessionNumber": ["0000320193-25-000001", "0000320193-25-000002", "0000320193-24-000123", "x"],
        "filingDate": ["2025-08-01", "2025-08-01", "2024-11-01", "2024-05-01"], "reportDate": ["", "2025-06-28", "2024-09-28", ""],
        "primaryDocument": ["a.htm", "q.htm", "aapl-20240928.htm", "old.htm"]}}})
    routes["Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"] = lambda r: httpx.Response(200, text=f"<html><body><p>{LONG}</p></body></html>")
    routes["Archives/edgar/data/320193/000032019325000002/q.htm"] = lambda r: httpx.Response(200, text="<p>Quarterly results</p>")


def test_sec_filings_and_documents(net):
    routes, seen = net
    _sec_routes(routes)
    assert sec.cik_for("aapl") == 320193
    latest = sec.latest_filings(320193)
    assert latest["annual"]["form"] == "10-K" and latest["quarterly"]["filed"] == "2025-08-01"
    assert latest["annual"]["url"].endswith("/320193/000032019324000123/aapl-20240928.htm")
    docs = reports.find_documents("AAPL", "Apple Inc.", "https://apple.com")
    assert [d["title"].split(" (")[0] for d in docs] == ["Apple Inc. 10-K", "Apple Inc. 10-Q"]
    assert "guidance" in docs[0]["text"] and docs[0]["source"] == "SEC EDGAR"
    ua = [r.headers["User-Agent"] for r in seen if "sec.gov" in str(r.url)]
    assert ua and all("@" in u for u in ua)  # SEC requires a contact address


def test_sec_long_history(net):
    routes, _ = net
    routes["company_tickers.json"] = lambda r: httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL"}})
    facts = {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": y - 2000, "form": "10-K", "filed": f"{y + 1}-02-01"} for y in range(2010, 2025)]}},
        "Revenues": {"units": {"USD": [
            {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 1000.0, "form": "10-K", "filed": f"{y + 1}-02-01"} for y in range(2010, 2025)]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 10.0, "form": "10-K", "filed": f"{y + 1}-02-01"} for y in range(2010, 2025)]}},
    }}}
    routes["companyfacts/CIK0000320193.json"] = lambda r: httpx.Response(200, json=facts)
    h = sec.long_history("AAPL")
    assert len(h["eps"]) == 15 and h["currency"] == "USD"
    assert h["revenue_per_share"]["2020-12-31"] == 100.0


def test_find_documents_web_path_downloads_pdf(net):
    routes, _ = net
    routes["company_tickers.json"] = lambda r: httpx.Response(200, json={})
    routes["duckduckgo.com/html"] = lambda r: httpx.Response(200, text='<a class="result__a" href="https://www.sap.com/ar-2025.pdf">SAP Annual Report 2025</a>')
    routes["sap.com/ar-2025.pdf"] = lambda r: httpx.Response(200, content=make_pdf("Revenue grew and the outlook was raised " * 200))
    docs = reports.find_documents("SAP.DE", "SAP SE", "https://www.sap.com")
    assert len(docs) == 1 and docs[0]["source"] == "web search"
    assert "outlook was raised" in docs[0]["text"]


def test_find_documents_nothing_found_is_not_cached(net):
    routes, seen = net
    routes["duckduckgo.com/html"] = lambda r: httpx.Response(500)
    assert reports.find_documents("SAP.DE", "SAP SE", None) == []
    n = len(seen)
    reports.find_documents("SAP.DE", "SAP SE", None)
    assert len(seen) > n  # retried instead of serving a cached empty result


def _llm(provider, base):
    db.set_setting("llm_provider", provider)
    db.set_setting("llm_api_key", "sk-test-0000")
    db.set_setting("llm_model", "m")
    db.set_setting("llm_base_url", base)


def test_llm_http_error_becomes_llm_error(net):
    routes, _ = net
    _llm("deepseek", "https://api.deepseek.test")
    routes["api.deepseek.test/chat/completions"] = lambda r: httpx.Response(401, json={"error": {"message": "Authentication Fails"}})
    with pytest.raises(llm.LLMError, match="HTTP 401.*Authentication Fails"):
        llm.test_connection()


def test_llm_anthropic_error_event(net):
    routes, _ = net
    _llm("anthropic", "https://api.anthropic.test")
    body = 'event: error\ndata: {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}\n\n'
    routes["api.anthropic.test/v1/messages"] = lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    with pytest.raises(llm.LLMError, match="Overloaded"):
        llm.test_connection()


def test_llm_ollama_needs_no_key(net):
    routes, seen = net
    _llm("ollama", "http://localhost:11434/v1")
    db.set_setting("llm_api_key", "")
    chunks = [{"choices": [{"delta": {"content": "O"}}]}, {"choices": [{"delta": {"content": "K"}}]}]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    routes["localhost:11434/v1/chat/completions"] = lambda r: httpx.Response(200, text=sse)
    assert llm.test_connection() == "OK"
    assert "Authorization" not in seen[-1].headers


def test_report_prompt_contains_sources_and_figures(net):
    routes, seen = net
    _sec_routes(routes)
    _llm("deepseek", "https://api.deepseek.test")
    routes["api.deepseek.test"] = lambda r: httpx.Response(200, text='data: {"choices":[{"delta":{"content":"## Done [S1]"}}]}\n\ndata: [DONE]\n\n')
    events = list(reports.stream_report("AAPL", {"name": "Apple Inc.", "pe": 30.0}, {"periods": [], "sections": []}, "growth", True))
    assert events[-1] == {"type": "done"}
    assert next(e for e in events if e["type"] == "sources")["sources"][0]["source"] == "SEC EDGAR"
    body = json.loads(seen[-1].content)
    prompt = body["messages"][1]["content"]
    assert '<source id="S1"' in prompt and "key_figures" in prompt and "guidance" in prompt
    assert "growth" in body["messages"][0]["content"].lower()


@pytest.mark.parametrize("status,ctype,body", [
    (200, "text/html", "<html>Your request has been identified as part of a network of automated tools</html>"),
    (403, "text/html", "<html>Request Rate Threshold Exceeded</html>"),
    (404, "application/json", '{"error": "not found"}'),
])
def test_sec_errors_say_what_the_sec_answered(net, status, ctype, body):
    routes, seen = net
    routes["company_tickers.json"] = lambda r: httpx.Response(status, text=body, headers={"content-type": ctype})
    with pytest.raises(sec.SECError) as err:
        sec.cik_for_or_raise("AAPL")
    msg = str(err.value)
    assert f"HTTP {status}" in msg and body[:30].strip("<>") .split(">")[0][:10] in msg
    if status == 403 or "html" in ctype:
        assert "refused" in msg
    assert sec.cik_for("AAPL") is None            # the app itself degrades gracefully
    ua = seen[-1].headers["User-Agent"]
    assert ua.startswith("FlowerTerminal ") and "@" in ua
    assert "gzip" in seen[-1].headers["Accept-Encoding"]


def _submissions(forms, dates, name="Apple Inc."):
    n = len(forms)
    return {"name": name, "filings": {"recent": {
        "form": forms, "accessionNumber": [f"0000320193-25-{i:06d}" for i in range(n)],
        "filingDate": dates, "reportDate": dates, "primaryDocument": [f"doc{i}.htm" for i in range(n)]}}}


def test_annual_report_found_even_after_many_quarterlies(net):
    """Found by live CI run #4: Apple's newest three filings were 10-Qs, so the 10-K was cut off."""
    routes, _ = net
    routes["company_tickers.json"] = lambda r: httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL"}})
    subs = _submissions(["10-Q", "8-K", "10-Q", "10-Q", "8-K", "10-K", "10-Q"],
                        ["2026-08-01", "2026-07-30", "2026-05-02", "2026-01-31", "2025-11-01", "2025-10-31", "2025-08-01"])
    routes["submissions/CIK0000320193.json"] = lambda r: httpx.Response(200, json=subs)
    routes["Archives/edgar/data/320193/"] = lambda r: httpx.Response(200, text=f"<p>{LONG}</p>")
    latest = sec.latest_filings(320193)
    assert latest["annual"]["form"] == "10-K" and latest["annual"]["filed"] == "2025-10-31"
    assert latest["quarterly"]["filed"] == "2026-08-01"
    docs = reports.find_documents("AAPL", "Apple Inc.", None)
    assert docs[0]["title"].startswith("Apple Inc. 10-K") and docs[1]["title"].startswith("Apple Inc. 10-Q")


def test_foreign_issuer_20f_and_amendment_fallback(net):
    routes, _ = net
    subs = _submissions(["6-K", "20-F/A", "6-K", "20-F"], ["2026-06-01", "2026-05-01", "2026-04-01", "2026-03-01"], "ASML")
    routes["submissions/CIK0000937966.json"] = lambda r: httpx.Response(200, json=subs)
    latest = sec.latest_filings(937966)
    assert latest["annual"]["form"] == "20-F"        # the original report is preferred over an amendment
    assert latest["quarterly"] is None
    only_amendment = _submissions(["10-K/A", "10-Q"], ["2026-02-01", "2026-05-01"])
    routes["submissions/CIK0000000001.json"] = lambda r: httpx.Response(200, json=only_amendment)
    assert sec.latest_filings(1)["annual"]["form"] == "10-K/A"
