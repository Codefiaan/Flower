"""Indicators, statement formatting, valuation history and the signal check.

Nothing in here is investment advice: the signal check is a transparent checklist of
common rules of thumb, each with its value and a one-line explanation.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd


# --- indicators ---------------------------------------------------------------

def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI: seeded with a simple average of the first n moves, then smoothed."""
    delta = close.diff().to_numpy()
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return pd.Series(out, index=close.index)
    gains, losses = np.clip(delta, 0, None), np.clip(-delta, 0, None)
    avg_g, avg_l = gains[1:n + 1].mean(), losses[1:n + 1].mean()
    for i in range(n, len(close)):
        if i > n:
            avg_g = (avg_g * (n - 1) + gains[i]) / n
            avg_l = (avg_l * (n - 1) + losses[i]) / n
        if avg_l == 0:
            out[i] = 50.0 if avg_g == 0 else 100.0
        else:
            out[i] = 100 - 100 / (1 + avg_g / avg_l)
    return pd.Series(out, index=close.index)


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, np.integer):
        return int(v)
    return v


def history_payload(df: pd.DataFrame, intraday: bool) -> list[dict]:
    """OHLCV rows plus SMA50/SMA200/RSI14 for the charts."""
    if df.empty:
        return []
    close = df["Close"]
    s50, s200, r = sma(close, 50), sma(close, 200), rsi(close)
    out = []
    for i, (ts, row) in enumerate(df.iterrows()):
        t = int(pd.Timestamp(ts).timestamp()) if intraday else pd.Timestamp(ts).strftime("%Y-%m-%d")
        out.append({
            "time": t, "open": _clean(row["Open"]), "high": _clean(row["High"]), "low": _clean(row["Low"]),
            "close": _clean(row["Close"]), "volume": _clean(row["Volume"]),
            "sma50": _clean(s50.iloc[i]), "sma200": _clean(s200.iloc[i]), "rsi": _clean(r.iloc[i]),
        })
    return out


# --- financial statements -----------------------------------------------------

# (Yahoo row label, display label, kind)
STATEMENT_ROWS = {
    "income": ("Income statement", [
        ("Total Revenue", "Revenue", "money"),
        ("Gross Profit", "Gross profit", "money"),
        ("Operating Income", "Operating income (EBIT)", "money"),
        ("EBITDA", "EBITDA", "money"),
        ("Research And Development", "R&D", "money"),
        ("Net Income", "Net income", "money"),
        ("Diluted EPS", "EPS (diluted)", "eps"),
    ]),
    "balance": ("Balance sheet", [
        ("Total Assets", "Total assets", "money"),
        ("Total Liabilities Net Minority Interest", "Total liabilities", "money"),
        ("Stockholders Equity", "Shareholders' equity", "money"),
        ("Cash And Cash Equivalents", "Cash", "money"),
        ("Total Debt", "Total debt", "money"),
        ("Net Debt", "Net debt", "money"),
        ("Current Assets", "Current assets", "money"),
        ("Current Liabilities", "Current liabilities", "money"),
    ]),
    "cashflow": ("Cash flow", [
        ("Operating Cash Flow", "Operating cash flow", "money"),
        ("Capital Expenditure", "Capex", "money"),
        ("Free Cash Flow", "Free cash flow", "money"),
        ("Cash Dividends Paid", "Dividends paid", "money"),
        ("Repurchase Of Capital Stock", "Share buybacks", "money"),
    ]),
}


def _row(df: pd.DataFrame, label: str, cols: list) -> list[float | None]:
    if df.empty or label not in df.index:
        return [None] * len(cols)
    return [_clean(df.at[label, c]) if c in df.columns else None for c in cols]


def _growth(values: list[float | None]) -> list[float | None]:
    out: list[float | None] = [None]
    for prev, cur in zip(values, values[1:]):
        out.append((cur / prev - 1) if prev not in (None, 0) and cur is not None and prev > 0 else None)
    return out


def _ratio(a: list, b: list) -> list[float | None]:
    return [(x / y) if x is not None and y not in (None, 0) else None for x, y in zip(a, b)]


def financials_payload(stmts: dict[str, pd.DataFrame]) -> dict:
    cols: list = []
    for df in stmts.values():
        for c in df.columns:
            if c not in cols:
                cols.append(c)
    cols = sorted(cols)[-8:]  # oldest -> newest
    sections = []
    for key, (title, spec) in STATEMENT_ROWS.items():
        df = stmts.get(key, pd.DataFrame())
        rows = []
        for label, display, kind in spec:
            vals = _row(df, label, cols)
            if all(v is None for v in vals):
                continue
            rows.append({"label": display, "kind": kind, "values": vals, "growth": _growth(vals)})
        if key == "income":
            rev = _row(df, "Total Revenue", cols)
            for label, display in (("Gross Profit", "Gross margin"), ("Operating Income", "Operating margin"),
                                   ("Net Income", "Net margin")):
                vals = _ratio(_row(df, label, cols), rev)
                if any(v is not None for v in vals):
                    rows.append({"label": display, "kind": "pct", "values": vals, "growth": [None] * len(cols)})
        sections.append({"key": key, "title": title, "rows": rows})
    return {"periods": [pd.Timestamp(c).strftime("%Y-%m-%d") for c in cols], "sections": sections}


# --- valuation history --------------------------------------------------------

def price_on(prices: pd.Series, when: pd.Timestamp) -> float | None:
    """Last close on or before `when` (None if the price history doesn't reach back that far)."""
    s = prices[prices.index <= when]
    if s.empty or (when - s.index[-1]).days > 10:
        return None
    return float(s.iloc[-1])


def valuation_history(prices: pd.Series, eps: dict, revenue_per_share: dict, fx: float = 1.0) -> dict:
    """P/E and P/S at each fiscal-year end.

    `eps` and `revenue_per_share` map period-end dates to values in the reporting currency;
    `fx` converts reporting currency into the quote currency.
    """
    points = []
    for when in sorted(set(eps) | set(revenue_per_share)):
        ts = pd.Timestamp(when)
        px = price_on(prices, ts)
        if px is None:
            continue
        e = eps.get(when)
        rps = revenue_per_share.get(when)
        points.append({
            "date": ts.strftime("%Y-%m-%d"),
            "price": px,
            "eps": e,
            "pe": px / (e * fx) if e and e > 0 else None,
            "ps": px / (rps * fx) if rps and rps > 0 else None,
        })
    pes = [p["pe"] for p in points if p["pe"] is not None]
    pss = [p["ps"] for p in points if p["ps"] is not None]
    return {
        "points": points,
        "pe_median": float(np.median(pes)) if pes else None,
        "pe_min": min(pes) if pes else None,
        "pe_max": max(pes) if pes else None,
        "ps_median": float(np.median(pss)) if pss else None,
    }


# --- signal check -------------------------------------------------------------

GOOD, NEUTRAL, BAD, NA = "good", "neutral", "bad", "na"


def _item(key: str, label: str, value: str, status: str, note: str) -> dict:
    return {"key": key, "label": label, "value": value, "status": status, "note": note}


def _pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def signal_check(info: dict, hist: pd.DataFrame, valuation: dict | None, calendar: dict | None) -> dict:
    items: list[dict] = []
    price = info.get("price")
    close = hist["Close"] if not hist.empty else pd.Series(dtype=float)

    # Trend
    s200 = sma(close, 200).iloc[-1] if len(close) >= 200 else None
    s50 = sma(close, 50).iloc[-1] if len(close) >= 50 else None
    if price and s200:
        d = price / s200 - 1
        items.append(_item("trend200", "Price vs. 200-day average", f"{d * 100:+.1f}%", GOOD if d > 0 else BAD,
                           "Above the 200-day average means the long-term trend is up; below means it is down."))
    else:
        items.append(_item("trend200", "Price vs. 200-day average", "n/a", NA, "Not enough price history."))
    if s50 and s200:
        up = s50 > s200
        items.append(_item("cross", "50-day vs. 200-day average", "Golden cross" if up else "Death cross",
                           GOOD if up else BAD,
                           "50-day above 200-day (golden cross) signals positive momentum; below (death cross) negative."))

    # Momentum
    r = rsi(close).iloc[-1] if len(close) > 15 else None
    if r is not None and not math.isnan(r):
        status = GOOD if r < 30 else BAD if r > 70 else NEUTRAL
        text = "oversold" if r < 30 else "overbought" if r > 70 else "neutral range"
        items.append(_item("rsi", "RSI (14 days)", f"{r:.0f} ({text})", status,
                           "Below 30 the stock has fallen unusually fast (possible entry); above 70 it has risen unusually fast."))

    # Drawdown
    high = info.get("high52")
    if price and high:
        dd = price / high - 1
        status = GOOD if dd < -0.2 else NEUTRAL
        items.append(_item("drawdown", "Distance from 52-week high", f"{dd * 100:.1f}%", status,
                           "A large discount to the 52-week high can mean opportunity - or a real problem. Check the news."))

    # Valuation vs. own history
    pe = info.get("pe")
    med = (valuation or {}).get("pe_median")
    if pe and med:
        d = pe / med - 1
        status = GOOD if d < -0.1 else BAD if d > 0.2 else NEUTRAL
        n = len([p for p in (valuation or {}).get("points", []) if p.get("pe")])
        items.append(_item("pe_hist", "P/E vs. own history", f"{pe:.1f} vs. median {med:.1f} ({d * 100:+.0f}%)", status,
                           f"Today's P/E compared with the median P/E at the last {n} fiscal-year ends."))
    elif pe:
        items.append(_item("pe_hist", "P/E vs. own history", f"{pe:.1f}", NA, "No usable earnings history."))
    else:
        items.append(_item("pe_hist", "P/E", "n/a", NA, "No P/E - the company may currently be loss-making."))

    fpe = info.get("forward_pe")
    if pe and fpe:
        status = GOOD if fpe < pe * 0.95 else BAD if fpe > pe * 1.05 else NEUTRAL
        items.append(_item("fpe", "Forward P/E vs. trailing P/E", f"{fpe:.1f} vs. {pe:.1f}", status,
                           "A lower forward P/E means analysts expect earnings to grow."))

    peg = info.get("peg")
    if peg:
        status = GOOD if 0 < peg < 1 else BAD if peg > 2 or peg < 0 else NEUTRAL
        items.append(_item("peg", "PEG ratio", f"{peg:.2f}", status,
                           "P/E divided by expected earnings growth. Below 1 is often seen as cheap relative to growth."))

    # Quality / balance sheet
    de = info.get("debt_to_equity")
    if de is not None:
        status = GOOD if de < 0.5 else BAD if de > 2 else NEUTRAL
        items.append(_item("debt", "Debt / equity", f"{de:.2f}", status,
                           "Below 0.5 is conservative, above 2 is high (banks and insurers are naturally higher)."))

    fcf, mcap = info.get("fcf"), info.get("market_cap")
    if fcf is not None and mcap:
        y = fcf / mcap
        status = GOOD if y > 0.05 else BAD if y < 0 else NEUTRAL
        items.append(_item("fcf", "Free-cash-flow yield", _pct(y), status,
                           "Free cash flow relative to market value. Above 5% is attractive, negative means cash burn."))

    rg = info.get("revenue_growth")
    if rg is not None:
        status = GOOD if rg > 0.05 else BAD if rg < 0 else NEUTRAL
        items.append(_item("growth", "Revenue growth (YoY)", _pct(rg), status,
                           "Latest quarter against the same quarter a year earlier."))

    # Analysts
    tgt = info.get("target_mean")
    if price and tgt:
        up = tgt / price - 1
        status = GOOD if up > 0.15 else BAD if up < 0 else NEUTRAL
        items.append(_item("target", "Analyst target (mean)", f"{tgt:.2f} ({up * 100:+.0f}%)", status,
                           f"Average target of {info.get('analysts') or '?'} analysts. Targets are often too optimistic."))

    # Events
    ed = (calendar or {}).get("earnings_date")
    if ed:
        try:
            days = (date.fromisoformat(ed) - date.today()).days
        except ValueError:
            days = None
        if days is not None and days >= 0:
            items.append(_item("earnings", "Next earnings report", f"{ed} (in {days} days)",
                               NEUTRAL, "Prices often move sharply around earnings - many wait for the numbers."))

    scored = [i for i in items if i["status"] in (GOOD, BAD)]
    good = sum(1 for i in scored if i["status"] == GOOD)
    return {
        "items": items,
        "good": good,
        "bad": len(scored) - good,
        "score": round(good / len(scored) * 100) if scored else None,
        "disclaimer": "Rules of thumb for orientation only - not investment advice. Data may be delayed or incorrect.",
    }


def light(score: int | None) -> str:
    if score is None:
        return NA
    return GOOD if score >= 60 else BAD if score < 40 else NEUTRAL
