"""Deterministic sample data (FLOWER_DEMO=1) for tests, screenshots and offline use.

All numbers are synthetic. They look plausible but are NOT real market data.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .base import Provider, empty_info

# symbol: (name, currency, sector, country, start price, pe, dividend yield)
UNIVERSE = {
    "AAPL": ("Apple Inc.", "USD", "Technology", "United States", 190.0, 29.0, 0.005),
    "MSFT": ("Microsoft Corporation", "USD", "Technology", "United States", 410.0, 34.0, 0.008),
    "NVDA": ("NVIDIA Corporation", "USD", "Technology", "United States", 120.0, 45.0, 0.0003),
    "AMZN": ("Amazon.com, Inc.", "USD", "Consumer Cyclical", "United States", 180.0, 38.0, None),
    "KO": ("The Coca-Cola Company", "USD", "Consumer Defensive", "United States", 62.0, 24.0, 0.031),
    "JNJ": ("Johnson & Johnson", "USD", "Healthcare", "United States", 155.0, 15.0, 0.032),
    "XOM": ("Exxon Mobil Corporation", "USD", "Energy", "United States", 110.0, 13.0, 0.034),
    "SAP.DE": ("SAP SE", "EUR", "Technology", "Germany", 210.0, 42.0, 0.011),
    "SIE.DE": ("Siemens AG", "EUR", "Industrials", "Germany", 175.0, 17.0, 0.029),
    "ALV.DE": ("Allianz SE", "EUR", "Financial Services", "Germany", 270.0, 12.0, 0.051),
    "BAS.DE": ("BASF SE", "EUR", "Basic Materials", "Germany", 45.0, 11.0, 0.075),
    "MBG.DE": ("Mercedes-Benz Group AG", "EUR", "Consumer Cyclical", "Germany", 62.0, 6.0, 0.085),
    "DTE.DE": ("Deutsche Telekom AG", "EUR", "Communication Services", "Germany", 28.0, 13.0, 0.03),
    "ASML.AS": ("ASML Holding N.V.", "EUR", "Technology", "Netherlands", 700.0, 36.0, 0.009),
    "NESN.SW": ("Nestle S.A.", "CHF", "Consumer Defensive", "Switzerland", 88.0, 19.0, 0.034),
    "SHEL.L": ("Shell plc", "GBp", "Energy", "United Kingdom", 2600.0, 12.0, 0.04),
}

INDICES = {
    "^GSPC": ("S&P 500", "USD", 5600.0), "^NDX": ("Nasdaq 100", "USD", 19500.0),
    "^GDAXI": ("DAX", "EUR", 19000.0), "^STOXX50E": ("Euro Stoxx 50", "EUR", 4900.0),
    "^N225": ("Nikkei 225", "JPY", 38000.0), "^VIX": ("VIX", "USD", 16.0),
    "^TNX": ("US 10Y yield", "USD", 4.1), "EURUSD=X": ("EUR/USD", "USD", 1.09),
    "GC=F": ("Gold", "USD", 2600.0), "CL=F": ("WTI crude", "USD", 72.0), "BTC-USD": ("Bitcoin", "USD", 62000.0),
}

FX_TO_EUR = {"EUR": 1.0, "USD": 0.92, "CHF": 1.06, "GBP": 1.19, "JPY": 0.0062}


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def _meta(symbol: str):
    if symbol in UNIVERSE:
        return UNIVERSE[symbol]
    if symbol in INDICES:
        name, cur, px = INDICES[symbol]
        return (name, cur, None, None, px, None, None)
    rng = np.random.default_rng(_seed(symbol))
    return (f"{symbol} (demo)", "USD", "Industrials", "United States", float(rng.uniform(20, 300)),
            float(rng.uniform(8, 40)), float(rng.uniform(0, 0.05)))


def _daily(symbol: str) -> pd.DataFrame:
    """Ten years of synthetic business-day prices ending today."""
    name, cur, _, _, start, _, _ = _meta(symbol)
    end = pd.Timestamp(date.today())
    idx = pd.bdate_range(end=end, periods=2600)
    rng = np.random.default_rng(_seed(symbol, "px"))
    rets = rng.normal(0.0004, 0.017, len(idx))
    close = start * 0.45 * np.exp(np.cumsum(rets))
    close = close / close[-1] * start  # make the latest price equal the nominal price
    spread = np.abs(rng.normal(0, 0.008, len(idx)))
    open_ = close * (1 + rng.normal(0, 0.004, len(idx)))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    vol = rng.integers(1_000_000, 20_000_000, len(idx)).astype(float)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)


class DemoProvider(Provider):
    name = "demo"

    def info(self, symbol: str) -> dict:
        name, cur, sector, country, _, pe, dy = _meta(symbol)
        h = _daily(symbol)
        last, prev = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
        year = h["Close"].iloc[-252:]
        rng = np.random.default_rng(_seed(symbol, "info"))
        d = empty_info(symbol)
        d.update(name=name, currency=cur, price=last, prev_close=prev, change=last - prev,
                 change_pct=(last / prev - 1) * 100, high52=float(year.max()), low52=float(year.min()),
                 quote_type="EQUITY" if symbol in UNIVERSE else "INDEX", exchange="DEMO",
                 financial_currency=cur)
        if symbol not in INDICES:
            shares = float(rng.uniform(0.5e9, 8e9))
            eps = last / pe
            revenue = shares * last / float(rng.uniform(1.5, 8))
            d.update(
                sector=sector, country=country, industry=sector, pe=pe, forward_pe=pe * float(rng.uniform(0.75, 1.05)),
                peg=float(rng.uniform(0.6, 3.0)), pb=float(rng.uniform(1, 12)), ps=last * shares / revenue,
                ev_ebitda=float(rng.uniform(6, 25)), div_yield=dy, payout=(dy or 0) * pe,
                gross_margin=float(rng.uniform(0.2, 0.7)), op_margin=float(rng.uniform(0.08, 0.4)),
                net_margin=float(rng.uniform(0.05, 0.3)), roe=float(rng.uniform(0.05, 0.45)),
                roa=float(rng.uniform(0.02, 0.2)), debt_to_equity=float(rng.uniform(0.1, 2.5)),
                current_ratio=float(rng.uniform(0.8, 2.5)), beta=float(rng.uniform(0.5, 1.6)),
                target_mean=last * float(rng.uniform(0.9, 1.3)), target_high=last * 1.45, target_low=last * 0.8,
                recommendation="buy", analysts=int(rng.integers(8, 45)), market_cap=last * shares,
                description=f"{name} is a demo company used to show the Flower Terminal without live data.",
                website="https://example.com", employees=int(rng.integers(5_000, 300_000)),
                revenue_growth=float(rng.uniform(-0.05, 0.25)), earnings_growth=float(rng.uniform(-0.1, 0.35)),
                fcf=revenue * float(rng.uniform(0.05, 0.25)), revenue=revenue, shares=shares, eps=eps,
                forward_eps=eps * 1.1,
            )
        return d

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        h = _daily(symbol)
        if period in ("1d", "5d"):
            days = 1 if period == "1d" else 5
            base = h.iloc[-days - 1:]
            rng = np.random.default_rng(_seed(symbol, period))
            steps = 78 if period == "1d" else 13
            rows, t0 = [], datetime.combine(date.today(), datetime.min.time()) - timedelta(days=days)
            price = float(base["Close"].iloc[0])
            for i in range(days * steps):
                price *= 1 + rng.normal(0, 0.002)
                rows.append((t0 + timedelta(minutes=(30 if days == 5 else 5) * i), price))
            idx = pd.DatetimeIndex([r[0] for r in rows])
            c = np.array([r[1] for r in rows])
            return pd.DataFrame({"Open": c, "High": c * 1.001, "Low": c * 0.999, "Close": c, "Volume": 1e5}, index=idx)
        n = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "10y": 2520, "max": 2600}.get(period)
        if period == "ytd":
            return h[h.index >= pd.Timestamp(date.today().year, 1, 1)]
        out = h.iloc[-(n or 252):]
        if period in ("5y", "10y", "max"):
            out = out.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        return out

    def statements(self, symbol: str, freq: str = "annual") -> dict[str, pd.DataFrame]:
        i = self.info(symbol)
        if i["revenue"] is None:
            return {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}
        rng = np.random.default_rng(_seed(symbol, freq))
        n = 5
        today = date.today()
        if freq == "quarterly":
            ends = [pd.Timestamp(today.year, 1, 1) - pd.offsets.QuarterEnd(k) for k in range(n)]
            scale = 0.25
        else:
            ends = [pd.Timestamp(today.year - 1 - k, 12, 31) for k in range(n)]
            scale = 1.0
        cols, inc, bal, cf = [], {}, {}, {}
        rev = i["revenue"] * scale
        for k, end in enumerate(ends):
            g = 1 / (1 + float(rng.uniform(-0.02, 0.15)))
            r = rev * (g ** k)
            gross, op = r * i["gross_margin"], r * i["op_margin"]
            net = r * i["net_margin"] * float(rng.uniform(0.8, 1.2))
            ebitda = op * 1.25
            eq = i["shares"] * i["price"] / (i["pb"] or 3) * (0.9 ** k)
            debt = eq * i["debt_to_equity"]
            ocf = net * float(rng.uniform(1.0, 1.5))
            capex = -r * float(rng.uniform(0.03, 0.1))
            cols.append(end)
            inc[end] = {"Total Revenue": r, "Cost Of Revenue": r - gross, "Gross Profit": gross,
                        "Operating Income": op, "EBITDA": ebitda, "Net Income": net,
                        "Diluted EPS": net / i["shares"], "Basic EPS": net / i["shares"] * 1.01,
                        "Research And Development": r * 0.08, "Diluted Average Shares": i["shares"]}
            bal[end] = {"Total Assets": eq + debt * 2.2, "Total Liabilities Net Minority Interest": debt * 2.2,
                        "Stockholders Equity": eq, "Total Debt": debt, "Net Debt": debt * 0.7,
                        "Cash And Cash Equivalents": debt * 0.3, "Current Assets": r * 0.5,
                        "Current Liabilities": r * 0.5 / i["current_ratio"], "Ordinary Shares Number": i["shares"]}
            cf[end] = {"Operating Cash Flow": ocf, "Capital Expenditure": capex, "Free Cash Flow": ocf + capex,
                       "Cash Dividends Paid": -net * (i["payout"] or 0), "Repurchase Of Capital Stock": -net * 0.2}
        return {"income": pd.DataFrame(inc)[cols], "balance": pd.DataFrame(bal)[cols], "cashflow": pd.DataFrame(cf)[cols]}

    def news(self, symbol: str) -> list[dict]:
        name = _meta(symbol)[0]
        now = int(datetime.now(timezone.utc).timestamp())
        templates = [
            "{n} reports quarterly results above expectations",
            "Analysts raise price target for {n}",
            "{n} announces new share buyback programme",
            "What the latest guidance means for {n} shareholders",
            "{n} faces regulatory questions in the EU",
        ]
        rng = np.random.default_rng(_seed(symbol, "news"))
        return [{"title": t.format(n=name) + " (demo)", "publisher": "Demo Wire", "url": f"https://example.com/news/{symbol}/{k}",
                 "time": now - int(rng.integers(600, 86400 * 6)), "summary": "Synthetic headline for demo mode.",
                 "symbol": symbol} for k, t in enumerate(templates)]

    def calendar(self, symbol: str) -> dict:
        if symbol in INDICES:
            return {"earnings_date": None, "ex_dividend_date": None, "dividend_date": None}
        off = _seed(symbol, "cal") % 60
        return {"earnings_date": (date.today() + timedelta(days=off + 3)).isoformat(),
                "ex_dividend_date": (date.today() + timedelta(days=off + 20)).isoformat(), "dividend_date": None}

    def search(self, query: str) -> list[dict]:
        q = query.lower()
        return [{"symbol": s, "name": m[0], "exchange": "DEMO", "type": "EQUITY"}
                for s, m in UNIVERSE.items() if q in s.lower() or q in m[0].lower()][:10]

    def screen(self, filters: dict) -> list[dict]:
        region_map = {"us": {"United States"}, "de": {"Germany"},
                      "eu": {"Germany", "Netherlands", "Switzerland", "United Kingdom"}}
        out = []
        for s in UNIVERSE:
            i = self.info(s)
            if filters.get("region") and i["country"] not in region_map.get(filters["region"], set()):
                continue
            if filters.get("sector") and i["sector"] != filters["sector"]:
                continue
            if filters.get("pe_min") is not None and (i["pe"] is None or i["pe"] < filters["pe_min"]):
                continue
            if filters.get("pe_max") is not None and (i["pe"] is None or i["pe"] > filters["pe_max"]):
                continue
            if filters.get("div_min") is not None and (i["div_yield"] or 0) < filters["div_min"]:
                continue
            if filters.get("roe_min") is not None and (i["roe"] or 0) < filters["roe_min"]:
                continue
            if filters.get("mcap_min") is not None and (i["market_cap"] or 0) * self.fx(i["currency"], "USD") < filters["mcap_min"]:
                continue
            out.append({k: i[k] for k in ("symbol", "name", "currency", "price", "change_pct", "market_cap", "pe",
                                          "forward_pe", "pb", "div_yield", "exchange", "high52", "low52")})
        return sorted(out, key=lambda r: -(r["market_cap"] or 0))

    def _fx_major(self, from_cur: str, to_cur: str) -> float:
        return FX_TO_EUR.get(from_cur, 1.0) / FX_TO_EUR.get(to_cur, 1.0)
