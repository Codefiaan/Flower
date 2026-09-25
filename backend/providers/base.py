"""Common provider interface. Every provider returns the same normalised shapes.

Shapes
------
info(symbol) -> dict with keys (missing values are None):
    symbol, name, currency, price, prev_close, change, change_pct, market_cap,
    pe, forward_pe, peg, pb, ps, ev_ebitda, div_yield (fraction), payout,
    gross_margin, op_margin, net_margin, roe, roa, debt_to_equity (ratio, not %),
    current_ratio, beta, high52, low52, target_mean, target_high, target_low,
    recommendation, analysts, sector, industry, country, description, website,
    employees, exchange, quote_type, revenue_growth, earnings_growth, fcf,
    revenue, shares, eps, forward_eps
history(symbol, period) -> pandas.DataFrame indexed by date with Open/High/Low/Close/Volume
statements(symbol, freq) -> {"income": df, "balance": df, "cashflow": df}
    each df: index = Yahoo row labels (e.g. "Total Revenue"), columns = period-end Timestamps
news(symbol) -> list of {title, publisher, url, time (epoch seconds), summary, symbol}
calendar(symbol) -> {earnings_date, ex_dividend_date, dividend_date} (ISO strings or None)
search(query) -> list of {symbol, name, exchange, type}
screen(filters) -> list of dicts with the list-row subset of info()
fx(from_currency, to_currency) -> float
"""
from __future__ import annotations

from typing import Any

import pandas as pd

INFO_KEYS = [
    "symbol", "name", "currency", "price", "prev_close", "change", "change_pct", "market_cap",
    "pe", "forward_pe", "peg", "pb", "ps", "ev_ebitda", "div_yield", "payout",
    "gross_margin", "op_margin", "net_margin", "roe", "roa", "debt_to_equity",
    "current_ratio", "beta", "high52", "low52", "target_mean", "target_high", "target_low",
    "recommendation", "analysts", "sector", "industry", "country", "description", "website",
    "employees", "exchange", "quote_type", "revenue_growth", "earnings_growth", "fcf",
    "revenue", "shares", "eps", "forward_eps", "financial_currency",
]

# Quote currencies that are quoted in 1/100 of the main unit.
MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def empty_info(symbol: str) -> dict[str, Any]:
    d = {k: None for k in INFO_KEYS}
    d["symbol"] = symbol
    return d


class Provider:
    name = "base"

    def info(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        raise NotImplementedError

    def statements(self, symbol: str, freq: str = "annual") -> dict[str, pd.DataFrame]:
        raise NotImplementedError

    def news(self, symbol: str) -> list[dict]:
        raise NotImplementedError

    def calendar(self, symbol: str) -> dict:
        raise NotImplementedError

    def search(self, query: str) -> list[dict]:
        raise NotImplementedError

    def screen(self, filters: dict) -> list[dict]:
        raise NotImplementedError

    def fx(self, from_cur: str | None, to_cur: str | None) -> float:
        """Conversion factor so that `amount_in_from * fx(from, to) == amount_in_to`.

        Handles minor-unit quote currencies such as GBp (pence) on either side.
        """
        f_major, f_div = MINOR_UNITS.get(from_cur, (from_cur, 1.0))
        t_major, t_div = MINOR_UNITS.get(to_cur, (to_cur, 1.0))
        factor = t_div / f_div
        if not f_major or not t_major or f_major == t_major:
            return factor
        return factor * self._fx_major(f_major, t_major)

    def _fx_major(self, from_cur: str, to_cur: str) -> float:
        raise NotImplementedError
