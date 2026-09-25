"""Yahoo provider parsing, tested against a fake `yfinance` with realistic payloads."""
import pandas as pd
import pytest

from backend import cache
from backend.providers import yahoo


def _hist(n=30, tz="America/New_York", freq="B", start="2025-01-01"):
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close,
                         "Volume": 1000.0, "Dividends": 0.0, "Stock Splits": 0.0})


INFO_AAPL = {
    "longName": "Apple Inc.", "currency": "USD", "currentPrice": 200.0, "regularMarketPreviousClose": 190.0,
    "marketCap": 3e12, "trailingPE": 30.0, "forwardPE": 27.0, "trailingPegRatio": 2.1, "priceToBook": 45.0,
    "dividendRate": 1.0, "dividendYield": 0.5, "trailingAnnualDividendYield": 0.0049, "debtToEquity": 150.0,
    "returnOnEquity": 1.5, "fiftyTwoWeekHigh": 260.0, "fiftyTwoWeekLow": 160.0, "targetMeanPrice": 240.0,
    "sector": "Technology", "financialCurrency": "USD", "fullExchangeName": "NasdaqGS", "freeCashflow": 1e11,
}


class FakeTicker:
    registry: dict = {}

    def __init__(self, symbol):
        self.symbol = symbol
        self.spec = FakeTicker.registry.get(symbol, {})

    @property
    def info(self):
        if isinstance(self.spec.get("info"), Exception):
            raise self.spec["info"]
        return self.spec.get("info", {})

    def history(self, period, interval, auto_adjust=True):
        h = self.spec.get("history")
        if isinstance(h, Exception):
            raise h
        return h if h is not None else pd.DataFrame()

    def get_news(self, count=10):
        n = self.spec.get("news", [])
        if isinstance(n, Exception):
            raise n
        return n

    @property
    def calendar(self):
        c = self.spec.get("calendar", {})
        if isinstance(c, Exception):
            raise c
        return c

    def __getattr__(self, name):
        if name in ("income_stmt", "quarterly_income_stmt", "balance_sheet", "quarterly_balance_sheet",
                    "cashflow", "quarterly_cashflow"):
            v = self.spec.get(name, pd.DataFrame())
            if isinstance(v, Exception):
                raise v
            return v
        raise AttributeError(name)


@pytest.fixture()
def fake_yf(monkeypatch):
    cache.clear()
    FakeTicker.registry = {}
    monkeypatch.setattr(yahoo.yf, "Ticker", FakeTicker)
    yield FakeTicker.registry
    cache.clear()


def test_info_maps_and_derives_fields(fake_yf):
    fake_yf["AAPL"] = {"info": INFO_AAPL}
    i = yahoo.YahooProvider().info("AAPL")
    assert i["name"] == "Apple Inc." and i["price"] == 200.0
    assert i["change"] == 10.0 and i["change_pct"] == pytest.approx(5.263, rel=1e-3)
    assert i["div_yield"] == pytest.approx(0.005)       # from dividendRate / price, not the percent field
    assert i["debt_to_equity"] == pytest.approx(1.5)    # Yahoo gives percent
    assert i["peg"] == 2.1 and i["exchange"] == "NasdaqGS"


def test_info_index_falls_back_to_history(fake_yf):
    fake_yf["^GDAXI"] = {"info": {"shortName": "DAX", "currency": "EUR"}, "history": _hist(tz="Europe/Berlin")}
    i = yahoo.YahooProvider().info("^GDAXI")
    assert i["price"] == 129.0 and i["pe"] is None


def test_info_failure_is_not_cached(fake_yf):
    fake_yf["MSFT"] = {"info": RuntimeError("429 Too Many Requests")}
    p = yahoo.YahooProvider()
    assert p.info("MSFT")["price"] is None
    fake_yf["MSFT"] = {"info": {**INFO_AAPL, "longName": "Microsoft"}}
    assert p.info("MSFT")["name"] == "Microsoft"   # recovered on the next call


def test_dividend_yield_fallback_and_nan_handling(fake_yf):
    fake_yf["X"] = {"info": {"currency": "EUR", "currentPrice": 50.0, "trailingAnnualDividendYield": 0.03,
                             "trailingPE": float("nan"), "marketCap": float("inf")}}
    i = yahoo.YahooProvider().info("X")
    assert i["div_yield"] == 0.03 and i["pe"] is None and i["market_cap"] is None


def test_history_daily_and_intraday(fake_yf):
    fake_yf["AAPL"] = {"history": _hist()}
    df = yahoo.YahooProvider().history("AAPL", "1y")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.tz is None and str(df.index[0].date()) == "2025-01-01"
    # intraday over several days: 1d keeps only the last trading day
    fake_yf["AAPL"] = {"history": _hist(n=3 * 78, freq="5min", start="2025-01-06 14:30")}
    cache.clear()
    one_day = yahoo.YahooProvider().history("AAPL", "1d")
    assert one_day.index.tz is None
    assert len({ts.date() for ts in one_day.index}) == 1


def test_history_failure_returns_empty_and_retries(fake_yf):
    fake_yf["BAD"] = {"history": RuntimeError("boom")}
    p = yahoo.YahooProvider()
    assert p.history("BAD", "1y").empty
    fake_yf["BAD"] = {"history": _hist()}
    assert len(p.history("BAD", "1y")) == 30


def test_statements(fake_yf):
    cols = [pd.Timestamp("2024-09-30"), pd.Timestamp("2023-09-30")]
    inc = pd.DataFrame({cols[0]: {"Total Revenue": 391e9, "Diluted EPS": 6.08}, cols[1]: {"Total Revenue": 383e9, "Diluted EPS": 6.13}})
    fake_yf["AAPL"] = {"income_stmt": inc, "balance_sheet": RuntimeError("x")}
    st = yahoo.YahooProvider().statements("AAPL", "annual")
    assert st["income"].at["Diluted EPS", cols[0]] == 6.08
    assert st["balance"].empty and st["cashflow"].empty


def test_news_new_and_legacy_formats(fake_yf):
    fake_yf["AAPL"] = {"news": [
        {"id": "1", "content": {"title": "New format", "summary": "s", "pubDate": "2025-05-01T10:00:00Z",
                                "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "https://a"},
                                "clickThroughUrl": None}},
        {"title": "Legacy", "publisher": "AP", "link": "https://b", "providerPublishTime": 1700000000},
        {"content": {"title": None}},
    ]}
    n = yahoo.YahooProvider().news("AAPL")
    assert [x["title"] for x in n] == ["New format", "Legacy"]
    assert n[0]["url"] == "https://a" and n[0]["publisher"] == "Reuters" and n[0]["time"] == 1746093600
    assert n[1]["time"] == 1700000000


def test_calendar_dict_and_dataframe(fake_yf):
    import datetime as dt

    fake_yf["AAPL"] = {"calendar": {"Earnings Date": [dt.date(2025, 7, 31), dt.date(2025, 8, 4)],
                                    "Ex-Dividend Date": dt.date(2025, 5, 12)}}
    c = yahoo.YahooProvider().calendar("AAPL")
    assert c == {"earnings_date": "2025-07-31", "ex_dividend_date": "2025-05-12", "dividend_date": None}
    fake_yf["OLD"] = {"calendar": pd.DataFrame({0: {"Earnings Date": pd.Timestamp("2025-01-30")}})}
    assert yahoo.YahooProvider().calendar("OLD")["earnings_date"] == "2025-01-30"
    fake_yf["ERR"] = {"calendar": RuntimeError("x")}
    assert yahoo.YahooProvider().calendar("ERR")["earnings_date"] is None


def test_search(monkeypatch):
    cache.clear()

    class FakeSearch:
        def __init__(self, q, **kw):
            self.quotes = [{"symbol": "SIE.DE", "longname": "Siemens AG", "exchDisp": "XETRA", "quoteType": "EQUITY"},
                           {"shortname": "no symbol"}]

    monkeypatch.setattr(yahoo.yf, "Search", FakeSearch)
    assert yahoo.YahooProvider().search("siemens") == [{"symbol": "SIE.DE", "name": "Siemens AG", "exchange": "XETRA", "type": "EQUITY"}]


def _flatten(q):
    """EquityQuery -> nested plain dict, to inspect what is sent to Yahoo."""
    return q.to_dict()


def test_screener_builds_query_and_parses(monkeypatch):
    cache.clear()
    seen = {}

    def fake_screen(query, size, sortField, sortAsc):
        seen["q"] = _flatten(query)
        return {"quotes": [{"symbol": "ALV.DE", "longName": "Allianz", "currency": "EUR", "regularMarketPrice": 300.0,
                            "regularMarketChangePercent": 1.2, "marketCap": 1.2e11, "trailingPE": 12.0,
                            "dividendRate": 15.0}]}

    monkeypatch.setattr(yahoo.yf, "screen", fake_screen)
    res = yahoo.YahooProvider().screen({"region": "eu", "pe_min": 0.1, "pe_max": 15, "div_min": 0.03, "sector": "Financial Services"})
    assert res[0]["symbol"] == "ALV.DE" and res[0]["div_yield"] == pytest.approx(0.05)
    text = str(seen["q"])
    assert "'OR'" in text and "'de'" in text and "'fr'" in text             # Europe = any of several regions
    assert "peratio.lasttwelvemonths" in text and "forward_dividend_yield" in text
    assert "3.0" in text                                                  # dividend passed in percent


def test_screener_error_raises_runtime_error(monkeypatch):
    cache.clear()

    def boom(*a, **k):
        raise ValueError("bad field")

    monkeypatch.setattr(yahoo.yf, "screen", boom)
    with pytest.raises(RuntimeError, match="Yahoo screener request failed"):
        yahoo.YahooProvider().screen({"region": "us"})


def test_fx_uses_currency_pair_and_minor_units(fake_yf):
    fake_yf["USDEUR=X"] = {"history": _hist()}
    fake_yf["GBPEUR=X"] = {"history": _hist()}
    p = yahoo.YahooProvider()
    assert p.fx("USD", "EUR") == 129.0
    assert p.fx("GBp", "EUR") == pytest.approx(1.29)
    with pytest.raises(RuntimeError):
        p.fx("CHF", "EUR")
