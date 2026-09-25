import pandas as pd
import pytest

from backend import portfolio
from backend.providers.base import Provider


class Fake(Provider):
    def info(self, symbol):
        return {"AAA": {"name": "A", "currency": "USD", "price": 110.0, "change": 1.0, "change_pct": 0.9, "sector": "Tech"},
                "BBB": {"name": "B", "currency": "GBp", "price": 500.0, "change": -5.0, "change_pct": -1.0, "sector": "Energy"}}[symbol]

    def history(self, symbol, period="1y"):
        idx = pd.bdate_range("2024-01-01", periods=5)
        return pd.DataFrame({"Close": [100.0, 100, 100, 100, 110] if symbol == "AAA" else [500.0] * 5}, index=idx)

    def _fx_major(self, a, b):
        return {("USD", "EUR"): 0.5, ("GBP", "EUR"): 1.2}[(a, b)]


def test_fx_minor_units():
    p = Fake()
    assert p.fx("GBp", "EUR") == pytest.approx(0.012)
    assert p.fx("EUR", "EUR") == 1.0
    assert p.fx("GBP", "GBp") == 100.0


def test_summarize_aggregates_lots_and_converts():
    lots = [{"symbol": "AAA", "shares": 1, "buy_price": 100.0}, {"symbol": "AAA", "shares": 1, "buy_price": 80.0},
            {"symbol": "BBB", "shares": 10, "buy_price": 400.0}]
    s = portfolio.summarize(lots, Fake(), "EUR")
    a = next(r for r in s["positions"] if r["symbol"] == "AAA")
    assert a["shares"] == 2 and a["avg_price"] == 90.0
    assert a["value"] == pytest.approx(110.0)       # 2 * 110 USD * 0.5
    assert a["pl"] == pytest.approx(20.0)           # (220 - 180) * 0.5
    b = next(r for r in s["positions"] if r["symbol"] == "BBB")
    assert b["value"] == pytest.approx(60.0)        # 10 * 500p = 50 GBP * 1.2
    assert s["total_value"] == pytest.approx(170.0)
    assert sum(x["pct"] for x in s["by_sector"]) == pytest.approx(100.0)


def test_value_history_respects_buy_dates():
    lots = [{"symbol": "AAA", "shares": 2, "buy_price": 100.0, "buy_date": "2024-01-04"}]
    pts = portfolio.value_history(lots, Fake(), "EUR", "1y")
    assert [p["time"] for p in pts] == ["2024-01-04", "2024-01-05"]
    assert pts[-1]["value"] == pytest.approx(110.0) and pts[-1]["cost"] == pytest.approx(100.0)
