"""SEC EDGAR (free, no key): long XBRL history and filing documents for US-listed companies.

The SEC asks every client to send a User-Agent with a contact address and to stay
below 10 requests per second (https://www.sec.gov/os/accessing-edgar-data).
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from ..cache import ttl_cache

log = logging.getLogger(__name__)

EPS_TAGS = [("us-gaap", "EarningsPerShareDiluted"), ("us-gaap", "EarningsPerShareBasic"),
            ("ifrs-full", "DilutedEarningsLossPerShare"), ("ifrs-full", "BasicEarningsLossPerShare")]
REVENUE_TAGS = [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                ("us-gaap", "SalesRevenueNet"), ("ifrs-full", "Revenue")]
SHARES_TAGS = [("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
               ("ifrs-full", "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted")]
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F"}


class SECError(RuntimeError):
    pass


def user_agent() -> str:
    """The format the SEC asks for: "<Company or app name> <contact e-mail>"."""
    from .. import prefs

    return f"FlowerTerminal {prefs.get('sec_contact')}"


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"},
                        timeout=30, follow_redirects=True)


def _get_json(url: str):
    """GET a JSON document from the SEC; errors say exactly what the SEC answered."""
    with _client() as c:
        r = c.get(url)
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or "json" not in ctype:
        snippet = " ".join(r.text[:200].split())
        hint = (" - the SEC refused the request; it blocks clients without a contact address and "
                "some shared cloud networks" if r.status_code in (403, 429) or "html" in ctype else "")
        raise SECError(f"{url} -> HTTP {r.status_code}, {ctype or 'no content type'}: {snippet!r}{hint}")
    return r.json()


@ttl_cache(86400)
def ticker_map() -> dict[str, int]:
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def cik_for_or_raise(symbol: str) -> int:
    """Like cik_for, but raises with the SEC's answer instead of hiding the problem."""
    cik = ticker_map().get(symbol.upper())
    if not cik:
        raise SECError(f"{symbol} not found in EDGAR's ticker list")
    return cik


def cik_for(symbol: str) -> int | None:
    if "." in symbol or "=" in symbol or symbol.startswith("^"):
        return None  # only US listings are in EDGAR's ticker map
    try:
        return ticker_map().get(symbol.upper())
    except Exception as exc:
        log.warning("SEC ticker map failed: %s", exc)
        return None


@ttl_cache(43200)
def company_facts(cik: int) -> dict:
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")


def annual_series(facts: dict, tags: list[tuple[str, str]]) -> tuple[dict[str, float], str | None]:
    """Fiscal-year values from annual reports, keyed by period end (latest filing wins)."""
    for taxonomy, tag in tags:
        node = facts.get("facts", {}).get(taxonomy, {}).get(tag)
        if not node:
            continue
        for unit, entries in node.get("units", {}).items():
            best: dict[str, tuple[str, float]] = {}
            for e in entries:
                if e.get("form") not in ANNUAL_FORMS or not e.get("start") or not e.get("end"):
                    continue
                days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                if not 350 <= days <= 380:
                    continue  # skip quarterly facts repeated inside annual reports
                prev = best.get(e["end"])
                if prev is None or e.get("filed", "") > prev[0]:
                    best[e["end"]] = (e.get("filed", ""), float(e["val"]))
            if best:
                return {k: v[1] for k, v in sorted(best.items())}, unit.split("/")[0]
    return {}, None


def long_history(symbol: str) -> dict | None:
    """EPS and revenue per share per fiscal year from XBRL, or None if unavailable."""
    cik = cik_for(symbol)
    if not cik:
        return None
    try:
        facts = company_facts(cik)
    except Exception as exc:
        log.warning("SEC companyfacts(%s) failed: %s", symbol, exc)
        return None
    eps, cur = annual_series(facts, EPS_TAGS)
    rev, _ = annual_series(facts, REVENUE_TAGS)
    shares, _ = annual_series(facts, SHARES_TAGS)
    rps = {k: rev[k] / shares[k] for k in rev if shares.get(k)}
    if not eps and not rps:
        return None
    return {"eps": eps, "revenue_per_share": rps, "currency": cur, "source": "SEC EDGAR XBRL"}


ANNUAL_REPORTS = ("10-K", "20-F", "40-F")


@ttl_cache(21600)
def latest_filings(cik: int) -> dict:
    """Newest annual report (10-K / 20-F / 40-F) and newest quarterly report (10-Q).

    Scans the whole recent-filings list: companies file several 10-Qs and 8-Ks after each
    10-K, so the annual report is often not among the newest few entries. An amendment
    (e.g. 10-K/A) is only used when no original annual report is listed.
    """
    data = _get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    recent = data.get("filings", {}).get("recent", {})
    found: dict[str, dict | None] = {"annual": None, "annual_amendment": None, "quarterly": None}
    for i, form in enumerate(recent.get("form", [])):  # newest first
        if form in ANNUAL_REPORTS:
            key = "annual"
        elif form.endswith("/A") and form[:-2] in ANNUAL_REPORTS:
            key = "annual_amendment"
        elif form == "10-Q":
            key = "quarterly"
        else:
            continue
        if found[key] is not None:
            continue
        acc = recent["accessionNumber"][i].replace("-", "")
        found[key] = {
            "form": form,
            "filed": recent["filingDate"][i],
            "period": (recent.get("reportDate") or [""] * (i + 1))[i],
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{recent['primaryDocument'][i]}",
            "company": data.get("name"),
        }
    return {"annual": found["annual"] or found["annual_amendment"], "quarterly": found["quarterly"]}


def fetch_document(url: str, max_bytes: int = 40_000_000) -> bytes:
    with _client() as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content[:max_bytes]
