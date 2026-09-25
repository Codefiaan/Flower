import numpy as np
import pandas as pd
import pytest

from backend import analysis


def test_sma():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert analysis.sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]


def test_rsi_extremes_and_range():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert analysis.rsi(up).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert analysis.rsi(down).iloc[-1] == pytest.approx(0.0)
    rng = np.random.default_rng(1)
    noisy = pd.Series(100 + rng.normal(0, 1, 300).cumsum())
    r = analysis.rsi(noisy).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_matches_wilder_reference():
    # Classic example series (Wilder, 14 periods); the reference value is ~70.5 for this window.
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert analysis.rsi(pd.Series(closes)).iloc[-1] == pytest.approx(70.5, abs=1.0)


def test_valuation_history():
    idx = pd.bdate_range("2020-01-01", "2024-12-31")
    prices = pd.Series(100.0, index=idx)
    eps = {"2021-12-31": 5.0, "2022-12-30": 10.0, "2023-12-29": -1.0}
    out = analysis.valuation_history(prices, eps, {}, fx=1.0)
    pes = [p["pe"] for p in out["points"]]
    assert pes == [20.0, 10.0, None]  # negative EPS -> no P/E
    assert out["pe_median"] == 15.0


def test_valuation_skips_dates_without_prices():
    prices = pd.Series(50.0, index=pd.bdate_range("2023-01-01", "2024-12-31"))
    out = analysis.valuation_history(prices, {"2015-12-31": 2.0, "2023-12-29": 2.5}, {}, fx=2.0)
    assert len(out["points"]) == 1
    assert out["points"][0]["pe"] == 10.0  # 50 / (2.5 * 2)


def test_financials_payload_growth_and_margins():
    cols = [pd.Timestamp("2023-12-31"), pd.Timestamp("2022-12-31")]
    inc = pd.DataFrame({cols[0]: {"Total Revenue": 120.0, "Net Income": 12.0},
                        cols[1]: {"Total Revenue": 100.0, "Net Income": 5.0}})
    out = analysis.financials_payload({"income": inc, "balance": pd.DataFrame(), "cashflow": pd.DataFrame()})
    assert out["periods"] == ["2022-12-31", "2023-12-31"]
    rows = {r["label"]: r for r in out["sections"][0]["rows"]}
    assert rows["Revenue"]["growth"][1] == pytest.approx(0.2)
    assert rows["Net margin"]["values"] == [pytest.approx(0.05), pytest.approx(0.1)]


def test_signal_check_scores():
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=300)
    hist = pd.DataFrame({"Close": np.linspace(50, 100, 300)}, index=idx)
    info = {"price": 100.0, "pe": 10.0, "forward_pe": 8.0, "high52": 100.0, "debt_to_equity": 0.3,
            "fcf": 8.0, "market_cap": 100.0, "revenue_growth": 0.1, "target_mean": 130.0, "analysts": 5, "peg": 0.8}
    out = analysis.signal_check(info, hist, {"pe_median": 15.0, "points": [{"pe": 15.0}]}, None)
    status = {i["key"]: i["status"] for i in out["items"]}
    assert status["trend200"] == "good" and status["cross"] == "good"
    assert status["rsi"] == "bad"  # straight line up = overbought
    assert status["pe_hist"] == "good" and status["debt"] == "good"
    assert 0 <= out["score"] <= 100
    assert "not investment advice" in out["disclaimer"]
