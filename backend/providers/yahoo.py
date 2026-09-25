"""Yahoo Finance provider (via the unofficial `yfinance` library, no API key needed).

Yahoo changes its responses from time to time; every accessor below is defensive and
returns None/empty values instead of raising when a field is missing.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from ..cache import ttl_cache, uncached
from .base import Provider, empty_info

log = logging.getLogger(__name__)

# period -> (yfinance period, interval)
PERIODS = {
    "1d": ("5d", "5m"),  # trimmed to the last trading day, so weekends still show Friday
    "5d": ("5d", "30m"),
    "1mo": ("1mo", "1d"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "ytd": ("ytd", "1d"),
    "1y": ("1y", "1d"),
    "2y": ("2y", "1d"),
    "5y": ("5y", "1wk"),
    "10y": ("10y", "1wk"),
    "max": ("max", "1mo"),
}

# Primary exchanges per screener region. Filtering on them keeps out secondary listings such as
# Samsung on the Stuttgart exchange (SSU.SG), whose quoted P/E mixes EUR prices with KRW earnings.
PRIMARY_EXCHANGES = {
    "us": ["NMS", "NYQ", "NGM", "NCM", "ASE"],
    "de": ["GER"], "gb": ["LSE"], "fr": ["PAR"], "ch": ["EBS"], "nl": ["AMS"], "jp": ["JPX"],
    "eu": ["GER", "PAR", "AMS", "MIL", "MCE", "EBS", "LSE", "STO", "CPH", "HEL", "OSL", "VIE", "ISE", "LIS", "BRU"],
}
ALL_PRIMARY = sorted({x for v in PRIMARY_EXCHANGES.values() for x in v})


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return _iso(v[0]) if v else None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return v.isoformat()[:10]
    return str(v)[:10]


def _div_yield(price: float | None, rate: float | None, fallback_fraction: float | None) -> float | None:
    # Yahoo switched `dividendYield` to percent units in 2025, so derive it from the
    # absolute dividend rate where possible and fall back to the trailing fraction.
    if price and rate:
        return rate / price
    return fallback_fraction


class YahooProvider(Provider):
    name = "yahoo"

    @ttl_cache(900)
    def info(self, symbol: str) -> dict[str, Any]:
        t = yf.Ticker(symbol)
        failed = False
        try:
            i = t.info or {}
        except Exception as exc:  # network errors, rate limits, unknown symbol
            log.warning("info(%s) failed: %s", symbol, exc)
            i, failed = {}, True
        d = empty_info(symbol)
        price = _num(i.get("currentPrice") or i.get("regularMarketPrice"))
        prev = _num(i.get("regularMarketPreviousClose") or i.get("previousClose"))
        if price is None:
            # Indices, FX and some ETFs have no `currentPrice`; fall back to recent history.
            h = self.history(symbol, "5d")
            if not h.empty:
                price = float(h["Close"].iloc[-1])
        d.update(
            name=i.get("longName") or i.get("shortName") or symbol,
            currency=i.get("currency"),
            price=price,
            prev_close=prev,
            market_cap=_num(i.get("marketCap")),
            pe=_num(i.get("trailingPE")),
            forward_pe=_num(i.get("forwardPE")),
            peg=_num(i.get("trailingPegRatio") or i.get("pegRatio")),
            pb=_num(i.get("priceToBook")),
            ps=_num(i.get("priceToSalesTrailing12Months")),
            ev_ebitda=_num(i.get("enterpriseToEbitda")),
            div_yield=_div_yield(price, _num(i.get("dividendRate")), _num(i.get("trailingAnnualDividendYield"))),
            payout=_num(i.get("payoutRatio")),
            gross_margin=_num(i.get("grossMargins")),
            op_margin=_num(i.get("operatingMargins")),
            net_margin=_num(i.get("profitMargins")),
            roe=_num(i.get("returnOnEquity")),
            roa=_num(i.get("returnOnAssets")),
            debt_to_equity=(_num(i.get("debtToEquity")) / 100) if _num(i.get("debtToEquity")) is not None else None,
            current_ratio=_num(i.get("currentRatio")),
            beta=_num(i.get("beta")),
            high52=_num(i.get("fiftyTwoWeekHigh")),
            low52=_num(i.get("fiftyTwoWeekLow")),
            target_mean=_num(i.get("targetMeanPrice")),
            target_high=_num(i.get("targetHighPrice")),
            target_low=_num(i.get("targetLowPrice")),
            recommendation=i.get("recommendationKey"),
            analysts=i.get("numberOfAnalystOpinions"),
            sector=i.get("sector"),
            industry=i.get("industry"),
            country=i.get("country"),
            description=i.get("longBusinessSummary"),
            website=i.get("website"),
            employees=i.get("fullTimeEmployees"),
            exchange=i.get("fullExchangeName") or i.get("exchange"),
            quote_type=i.get("quoteType"),
            revenue_growth=_num(i.get("revenueGrowth")),
            earnings_growth=_num(i.get("earningsGrowth")),
            fcf=_num(i.get("freeCashflow")),
            revenue=_num(i.get("totalRevenue")),
            shares=_num(i.get("sharesOutstanding")),
            eps=_num(i.get("trailingEps")),
            forward_eps=_num(i.get("forwardEps")),
            financial_currency=i.get("financialCurrency") or i.get("currency"),
        )
        if price is not None and prev:
            d["change"] = price - prev
            d["change_pct"] = (price / prev - 1) * 100
        return uncached(d) if failed else d

    @ttl_cache(300)
    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        yp, interval = PERIODS.get(period, ("1y", "1d"))
        try:
            df = yf.Ticker(symbol).history(period=yp, interval=interval, auto_adjust=True)
        except Exception as exc:
            log.warning("history(%s) failed: %s", symbol, exc)
            df = None
        if df is None or df.empty:
            return uncached(pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        if interval.endswith("m"):
            if period == "1d":
                last_day = df.index[-1].date()
                df = df[[ts.date() == last_day for ts in df.index]]
            idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
            df.index = idx.tz_convert("UTC").tz_localize(None)
        else:
            df.index = pd.to_datetime(df.index.date)
        return df

    @ttl_cache(43200)
    def statements(self, symbol: str, freq: str = "annual") -> dict[str, pd.DataFrame]:
        t = yf.Ticker(symbol)
        q = freq == "quarterly"
        out, failed = {}, False
        for key, annual, quarterly in (
            ("income", "income_stmt", "quarterly_income_stmt"),
            ("balance", "balance_sheet", "quarterly_balance_sheet"),
            ("cashflow", "cashflow", "quarterly_cashflow"),
        ):
            try:
                df = getattr(t, quarterly if q else annual)
            except Exception as exc:
                log.warning("statements(%s, %s) failed: %s", symbol, key, exc)
                df, failed = None, True
            out[key] = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
        return uncached(out) if failed else out

    @ttl_cache(600)
    def news(self, symbol: str) -> list[dict]:
        try:
            raw = yf.Ticker(symbol).get_news(count=20) or []
        except Exception as exc:
            log.warning("news(%s) failed: %s", symbol, exc)
            return uncached([])
        items = []
        for n in raw:
            c = n.get("content")
            if isinstance(c, dict):  # format since early 2025
                url = (c.get("clickThroughUrl") or {}).get("url") or (c.get("canonicalUrl") or {}).get("url")
                ts = c.get("pubDate") or c.get("displayTime")
                try:
                    epoch = int(pd.Timestamp(ts).timestamp()) if ts else None
                except Exception:
                    epoch = None
                items.append({
                    "title": c.get("title"),
                    "publisher": (c.get("provider") or {}).get("displayName"),
                    "url": url,
                    "time": epoch,
                    "summary": c.get("summary") or "",
                    "symbol": symbol,
                })
            else:  # legacy format
                items.append({
                    "title": n.get("title"),
                    "publisher": n.get("publisher"),
                    "url": n.get("link"),
                    "time": n.get("providerPublishTime"),
                    "summary": "",
                    "symbol": symbol,
                })
        return [i for i in items if i["title"]]

    @ttl_cache(3600)
    def calendar(self, symbol: str) -> dict:
        try:
            cal = yf.Ticker(symbol).calendar
            if cal is None:
                cal = {}
        except Exception as exc:
            log.warning("calendar(%s) failed: %s", symbol, exc)
            return uncached({"earnings_date": None, "ex_dividend_date": None, "dividend_date": None})
        if isinstance(cal, pd.DataFrame):  # very old yfinance versions
            cal = cal.iloc[:, 0].to_dict() if not cal.empty else {}
        return {
            "earnings_date": _iso(cal.get("Earnings Date")),
            "ex_dividend_date": _iso(cal.get("Ex-Dividend Date")),
            "dividend_date": _iso(cal.get("Dividend Date")),
        }

    @ttl_cache(3600)
    def search(self, query: str) -> list[dict]:
        try:
            quotes = yf.Search(query, max_results=10, news_count=0).quotes
        except Exception as exc:
            log.warning("search(%s) failed: %s", query, exc)
            return uncached([])
        return [
            {
                "symbol": q.get("symbol"),
                "name": q.get("longname") or q.get("shortname") or q.get("symbol"),
                "exchange": q.get("exchDisp") or q.get("exchange"),
                "type": q.get("quoteType"),
            }
            for q in quotes
            if q.get("symbol")
        ]

    @ttl_cache(1800)
    def _screen_cached(self, key: tuple) -> list[dict]:
        filters = dict(key)
        Q = yf.EquityQuery
        parts = []
        region = filters.get("region")
        exchanges = PRIMARY_EXCHANGES.get(region, ALL_PRIMARY) if region in PRIMARY_EXCHANGES or not region else None
        if exchanges:
            parts.append(Q("is-in", ["exchange", *exchanges]))
        if region and region not in PRIMARY_EXCHANGES:
            parts.append(Q("eq", ["region", region]))
        if filters.get("sector"):
            parts.append(Q("eq", ["sector", filters["sector"]]))
        if filters.get("pe_min") is not None:
            parts.append(Q("gte", ["peratio.lasttwelvemonths", filters["pe_min"]]))
        if filters.get("pe_max") is not None:
            parts.append(Q("lte", ["peratio.lasttwelvemonths", filters["pe_max"]]))
        if filters.get("div_min") is not None:  # Yahoo expects percent here
            parts.append(Q("gte", ["forward_dividend_yield", filters["div_min"] * 100]))
        if filters.get("roe_min") is not None:
            parts.append(Q("gte", ["returnonequity.lasttwelvemonths", filters["roe_min"] * 100]))
        if filters.get("mcap_min") is not None:
            parts.append(Q("gte", ["intradaymarketcap", filters["mcap_min"]]))
        if not parts:
            parts.append(Q("gte", ["intradaymarketcap", 1e10]))
        query = parts[0] if len(parts) == 1 else Q("and", parts)
        res = yf.screen(query, size=100, sortField="intradaymarketcap", sortAsc=False)
        out = []
        for q in (res or {}).get("quotes", []):
            price = _num(q.get("regularMarketPrice"))
            out.append({
                "symbol": q.get("symbol"),
                "name": q.get("longName") or q.get("shortName") or q.get("symbol"),
                "currency": q.get("currency"),
                "price": price,
                "change_pct": _num(q.get("regularMarketChangePercent")),
                "market_cap": _num(q.get("marketCap")),
                "pe": _num(q.get("trailingPE")),
                "forward_pe": _num(q.get("forwardPE")),
                "pb": _num(q.get("priceToBook")),
                "div_yield": _div_yield(price, _num(q.get("dividendRate")), _num(q.get("trailingAnnualDividendYield"))),
                "exchange": q.get("fullExchangeName") or q.get("exchange"),
                "high52": _num(q.get("fiftyTwoWeekHigh")),
                "low52": _num(q.get("fiftyTwoWeekLow")),
            })
        # Safety net: Yahoo filters on the company's P/E, but a listing's quoted P/E can differ.
        lo, hi = filters.get("pe_min"), filters.get("pe_max")
        if lo is not None or hi is not None:
            kept = [r for r in out if r["pe"] is None or ((lo is None or r["pe"] >= lo) and (hi is None or r["pe"] <= hi * 1.05))]
            if len(kept) < len(out):
                log.info("screener dropped %d rows whose quoted P/E contradicts the filter", len(out) - len(kept))
            out = kept
        return out

    def screen(self, filters: dict) -> list[dict]:
        key = tuple(sorted((k, v) for k, v in filters.items() if v not in (None, "")))
        try:
            return self._screen_cached(key)
        except Exception as exc:
            log.warning("screen(%s) failed: %s", filters, exc)
            raise RuntimeError(f"Yahoo screener request failed: {exc}") from exc

    @ttl_cache(3600)
    def _fx_major(self, from_cur: str, to_cur: str) -> float:
        h = self.history(f"{from_cur}{to_cur}=X", "5d")
        if h.empty:
            raise RuntimeError(f"No FX rate for {from_cur}->{to_cur}")
        return float(h["Close"].iloc[-1])
