"""The health check itself: offline run, report file, and live checks against faked data sources."""
import pandas as pd
import pytest

from backend import cache, check, db


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    db.set_path(tmp_path / "check.db")
    cache.clear()
    check.RESULTS.clear()
    yield
    check.RESULTS.clear()


def statuses():
    return {name: status for status, name, _ in check.RESULTS}


def test_offline_check_passes_and_writes_report(tmp_path):
    report = tmp_path / "report.txt"
    assert check.main(["--report", str(report)]) == 0
    s = statuses()
    assert s["Required packages"] == "PASS" and s["App API (demo data)"] == "PASS" and s["App pages"] == "PASS"
    text = report.read_text(encoding="utf-8")
    assert "Flower check report" in text and "App API (demo data)" in text


def test_llm_not_configured_is_a_warning():
    assert check.main(["--llm"]) == 0
    assert statuses()["AI model connection"] == "WARN"


def test_unreachable_yahoo_skips_live_checks(monkeypatch):
    monkeypatch.setattr(check, "network_checks", lambda: False)
    assert check.main(["--live"]) == 1
    assert statuses()["Live data"] == "FAIL"


class FakeYahoo:
    def info(self, s):
        return {"name": s, "price": 100.0, "currency": "USD", "pe": 20.0, "market_cap": 1e9, "high52": 120.0,
                "low52": 80.0, "sector": "Tech"}

    def history(self, s, period):
        n = 250 if period == "1y" else 78
        return pd.DataFrame({"Close": [100.0] * n})

    def statements(self, s, freq):
        inc = pd.DataFrame({pd.Timestamp("2024-12-31"): {"Total Revenue": 1.0, "Net Income": 1.0, "Diluted EPS": 1.0}})
        return {"income": inc, "balance": inc, "cashflow": inc}

    def news(self, s):
        return [{"title": "Hello", "url": "https://x", "time": 1}]

    def calendar(self, s):
        return {"earnings_date": "2025-01-30"}

    def search(self, q):
        return [{"symbol": "SIE.DE"}]

    def screen(self, f):
        return [{"symbol": "ALV.DE", "pe": 12.0}]

    def fx(self, a, b):
        return 0.92 if a == "USD" else 0.0118


def test_live_checks_with_plausible_data(monkeypatch):
    from backend.providers import sec, yahoo

    monkeypatch.setattr(yahoo, "YahooProvider", FakeYahoo)
    monkeypatch.setattr(sec, "long_history", lambda s: {"eps": {f"{y}-12-31": 1.0 for y in range(2010, 2025)}, "currency": "USD"})
    monkeypatch.setattr(sec, "cik_for", lambda s: 320193)
    monkeypatch.setattr(sec, "cik_for_or_raise", lambda s: 320193)
    monkeypatch.setattr(sec, "company_facts", lambda cik: {})
    monkeypatch.setattr(sec, "recent_filings", lambda cik: [{"form": "10-K", "filed": "2024-11-01"}])
    check.live_checks()
    s = statuses()
    assert all(v == "PASS" for v in s.values()), check.RESULTS


def test_live_checks_report_format_problems(monkeypatch):
    from backend.providers import sec, yahoo

    class Broken(FakeYahoo):
        def statements(self, s, freq):
            inc = pd.DataFrame({pd.Timestamp("2024-12-31"): {"Revenue": 1.0}})
            return {"income": inc, "balance": inc, "cashflow": pd.DataFrame()}

        def screen(self, f):
            return [{"symbol": "X", "pe": 40.0}]

        def info(self, s):
            return {**super().info(s), "pe": None, "market_cap": None, "sector": None}

    monkeypatch.setattr(yahoo, "YahooProvider", Broken)
    monkeypatch.setattr(sec, "long_history", lambda s: None)
    monkeypatch.setattr(sec, "cik_for", lambda s: None)

    def blocked(s):
        raise sec.SECError("https://www.sec.gov/files/company_tickers.json -> HTTP 403, text/html: 'blocked'")

    monkeypatch.setattr(sec, "cik_for_or_raise", blocked)
    check.live_checks()
    by_name = {n: (st, d) for st, n, d in check.RESULTS}
    assert by_name["Yahoo statements AAPL"][0] == "FAIL" and "cashflow statement empty" in by_name["Yahoo statements AAPL"][1]
    assert by_name["Yahoo screener"][0] == "FAIL" and "P/E filter not applied" in by_name["Yahoo screener"][1]
    assert by_name["Yahoo quote AAPL"][0] == "WARN" and "missing fields" in by_name["Yahoo quote AAPL"][1]
    assert by_name["SEC EDGAR XBRL history"][0] == "FAIL" and "HTTP 403" in by_name["SEC EDGAR XBRL history"][1]


def test_sec_can_be_optional_in_ci(monkeypatch):
    from backend.providers import sec

    def blocked(s):
        raise sec.SECError("HTTP 403")

    monkeypatch.setenv("FLOWER_CHECK_SEC_OPTIONAL", "1")
    monkeypatch.setattr(sec, "cik_for_or_raise", blocked)
    monkeypatch.setattr(sec, "long_history", lambda s: None)
    check.check("SEC EDGAR filings", lambda: sec.cik_for_or_raise("AAPL"), warn_only=check._sec_optional())
    assert statuses()["SEC EDGAR filings"] == "WARN"


def test_network_check_explains_blocked_connection(monkeypatch):
    import httpx

    def refuse(*a, **k):
        raise httpx.ConnectError("proxy said no")

    monkeypatch.setattr(httpx, "get", refuse)
    assert check.network_checks() is False
    detail = dict((n, d) for _, n, d in check.RESULTS)["Network: Yahoo Finance"]
    assert "firewall, proxy or no internet" in detail


def test_placeholder_sec_contact_is_a_warning(monkeypatch):
    from backend import prefs
    from backend.config import Settings

    monkeypatch.setattr(prefs, "settings", Settings(sec_contact=prefs.PLACEHOLDER_SEC_CONTACT))
    check.environment_checks()
    assert statuses()["SEC contact address"] == "WARN"
    check.RESULTS.clear()
    prefs.update({"sec_contact": "eric@example.org"})
    check.environment_checks()
    assert statuses()["SEC contact address"] == "PASS"
