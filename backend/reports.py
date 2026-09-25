"""Find annual reports, extract the relevant text and let the configured LLM analyse them.

US listings: SEC EDGAR filings (10-K / 20-F / 10-Q), no key needed.
Everything else: a web search for the investor-relations annual report (PDF), using
Tavily or Brave Search when a key is configured, otherwise DuckDuckGo's HTML page
(best effort - it has no official API and may block or change).
"""
from __future__ import annotations

import html
import io
import json
import logging
import re
from datetime import date, datetime
from typing import Iterator
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import db, llm
from .cache import ttl_cache
from .config import settings
from .providers import sec

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
CONTEXT_CHARS = 60_000  # ~15k tokens of source text; fits DeepSeek/OpenAI/Claude context windows
KEYWORDS = {
    "outlook": 5, "guidance": 5, "forecast": 4, "expect": 2, "risk": 3, "segment": 3, "revenue": 2,
    "net sales": 2, "margin": 2, "free cash flow": 3, "operating income": 2, "ebit": 2, "dividend": 2,
    "buyback": 2, "repurchase": 2, "management's discussion": 5, "results of operations": 4,
    "liquidity": 2, "debt": 1, "competition": 2, "backlog": 2, "order intake": 3, "strategy": 2,
    "headwind": 2, "impairment": 3, "restructuring": 3, "litigation": 2,
}

LENSES = {
    "general": "a balanced equity research analyst",
    "value": "a value investor in the tradition of Benjamin Graham: focus on margin of safety, balance-sheet "
             "strength, earnings stability, dividends and valuation versus history",
    "growth": "a growth/quality investor: focus on revenue growth, reinvestment, competitive moat, unit "
              "economics, margins trajectory and management execution",
}


# --- search ------------------------------------------------------------------

def _search_tavily(query: str, key: str) -> list[dict]:
    r = httpx.post("https://api.tavily.com/search", timeout=30,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"api_key": key, "query": query, "max_results": 8})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", "")} for x in r.json().get("results", [])]


def _search_brave(query: str, key: str) -> list[dict]:
    r = httpx.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 10},
                  headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", "")} for x in r.json().get("web", {}).get("results", [])]


def _search_duckduckgo(query: str) -> list[dict]:
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query}, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    out = []
    for href, title in re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        href = html.unescape(href)
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        out.append({"title": re.sub("<[^>]+>", "", html.unescape(title)), "url": href})
    return out


def web_search(query: str) -> list[dict]:
    provider = db.get_setting("search_provider", "duckduckgo")
    key = db.get_setting("search_api_key", "")
    if provider == "tavily" and key:
        return _search_tavily(query, key)
    if provider == "brave" and key:
        return _search_brave(query, key)
    return _search_duckduckgo(query)


def _domain(url: str | None) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def rank_results(results: list[dict], company_site: str | None) -> list[dict]:
    site = _domain(company_site)
    base = site.split(".")[-2] if site.count(".") >= 1 else site

    def score(r: dict) -> int:
        u, t = r["url"].lower(), r["title"].lower()
        s = 0
        s += 5 if u.endswith(".pdf") or ".pdf?" in u else 0
        s += 4 if base and base in _domain(u) else 0
        s += 3 if any(w in u + t for w in ("annual-report", "annual report", "annualreport", "geschaeftsbericht",
                                              "geschäftsbericht", "integrated report", "jahresbericht")) else 0
        s += 2 if "investor" in u else 0
        s -= 4 if any(w in u for w in ("wikipedia", "youtube", "facebook", "twitter", "linkedin")) else 0
        return s

    return sorted(results, key=score, reverse=True)


# --- text extraction ---------------------------------------------------------

def html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"(?is)<(script|style|ix:header)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def pdf_to_text(raw: bytes, max_pages: int = 250) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    parts = []
    for i, page in enumerate(reader.pages):
        if i >= max_pages:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return re.sub(r"[ \t]+", " ", "\n".join(parts))


def to_text(raw: bytes, url: str) -> str:
    if raw[:5] == b"%PDF-" or url.lower().split("?")[0].endswith(".pdf"):
        return pdf_to_text(raw)
    return html_to_text(raw)


def select_passages(text: str, budget: int) -> str:
    """Keep the chunks that talk most about results, outlook and risks, in document order."""
    if len(text) <= budget:
        return text
    size = 2500
    chunks = [text[i:i + size] for i in range(0, len(text), size)]
    scored = []
    for idx, ch in enumerate(chunks):
        low = ch.lower()
        s = sum(w * low.count(k) for k, w in KEYWORDS.items())
        s += 0.5 * len(re.findall(r"\d[\d,.]{3,}", ch)) ** 0.5  # numbers-heavy chunks are usually useful
        scored.append((s, idx))
    keep = sorted(idx for _, idx in sorted(scored, reverse=True)[: max(1, budget // size)])
    if 0 not in keep:  # the opening pages usually contain the summary / letter to shareholders
        keep = [0] + keep[:-1]
    return "\n[...]\n".join(chunks[i] for i in keep)


# --- document discovery ------------------------------------------------------

def _download(url: str) -> bytes:
    if "sec.gov" in url:
        return sec.fetch_document(url)
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content[:40_000_000]


@ttl_cache(21600)
def find_documents(symbol: str, name: str, website: str | None) -> list[dict]:
    """Return up to two source documents: {title, url, source, text}."""
    docs: list[dict] = []
    if settings.demo:
        return docs  # demo mode never goes online
    cik = sec.cik_for(symbol)
    if cik:
        try:
            filings = sec.recent_filings(cik)
        except Exception as exc:
            log.warning("EDGAR filings for %s failed: %s", symbol, exc)
            filings = []
        annual = [f for f in filings if f["form"] != "10-Q"][:1]
        quarterly = [f for f in filings if f["form"] == "10-Q"][:1]
        for f in annual + quarterly:
            try:
                text = to_text(_download(f["url"]), f["url"])
            except Exception as exc:
                log.warning("download %s failed: %s", f["url"], exc)
                continue
            docs.append({"title": f"{f['company']} {f['form']} (period {f['period']}, filed {f['filed']})",
                         "url": f["url"], "source": "SEC EDGAR", "text": text})
    if docs:
        return docs

    year = date.today().year
    for q in (f"{name} annual report {year - 1} pdf", f"{name} annual report {year - 2} pdf investor relations"):
        try:
            results = rank_results(web_search(q), website)
        except Exception as exc:
            log.warning("web search failed: %s", exc)
            results = []
        for r in results[:4]:
            try:
                text = to_text(_download(r["url"]), r["url"])
            except Exception as exc:
                log.info("skip %s: %s", r["url"], exc)
                continue
            if len(text) > 5000:
                docs.append({"title": r["title"] or r["url"], "url": r["url"], "source": "web search", "text": text})
                return docs
    return docs


# --- prompts -----------------------------------------------------------------

def _figures(info: dict, fin: dict) -> str:
    keys = ["name", "currency", "price", "market_cap", "pe", "forward_pe", "peg", "pb", "ps", "ev_ebitda", "div_yield",
            "payout", "gross_margin", "op_margin", "net_margin", "roe", "debt_to_equity", "fcf", "revenue_growth",
            "target_mean", "sector", "industry", "financial_currency"]
    figures = {k: info.get(k) for k in keys}
    table = {s["title"]: {r["label"]: dict(zip(fin.get("periods", []), r["values"])) for r in s["rows"]}
             for s in fin.get("sections", [])}
    return json.dumps({"market_data": figures, "statements": table}, default=str)[:15_000]


def build_context(info: dict, fin: dict, docs: list[dict]) -> tuple[str, list[dict]]:
    budget = CONTEXT_CHARS // max(1, len(docs))
    sources, parts = [], []
    for i, d in enumerate(docs, 1):
        sources.append({"id": f"S{i}", "title": d["title"], "url": d["url"], "source": d["source"]})
        parts.append(f"<source id=\"S{i}\" title=\"{d['title']}\">\n{select_passages(d['text'], budget)}\n</source>")
    context = (f"<key_figures source=\"Yahoo Finance\">\n{_figures(info, fin)}\n</key_figures>\n\n" + "\n\n".join(parts))
    return context, sources


def system_prompt(lens: str, language: str) -> str:
    return (
        f"You are {LENSES.get(lens, LENSES['general'])}. You write for a private investor who wants to understand a "
        "company, not for a professional. You never give buy or sell recommendations; you explain facts, trends and "
        "risks. Base every statement on the provided sources and key figures and cite them inline like [S1] or "
        "[Yahoo]. If something is not in the sources, say so instead of guessing. Use Markdown with short sections "
        f"and bullet points. Write in {language}."
    )


REPORT_TASK = """Analyse the company using the annual report excerpts and key figures below.

Structure:
## Business in brief
## Growth & profitability (revenue, margins, earnings - with numbers and trend)
## Balance sheet & cash flow (debt, liquidity, free cash flow, dividends/buybacks)
## Management outlook / guidance
## Main risks
## Red flags & things to watch
## Valuation context (P/E etc. from the key figures, compared with growth)
## Summary in 5 bullet points

{context}"""


def stream_report(symbol: str, info: dict, fin: dict, lens: str = "general", refresh: bool = False) -> Iterator[dict]:
    kind = f"report:{lens}"
    cached = None if refresh else db.get_report(symbol, kind)
    if cached:
        yield {"type": "sources", "sources": json.loads(cached["sources"]), "cached": cached["created"]}
        yield {"type": "delta", "text": cached["content"]}
        yield {"type": "done"}
        return
    if not llm.load_config().configured:
        raise llm.LLMError("No LLM configured. Add a provider and API key under Settings.")
    yield {"type": "status", "text": "Searching for annual reports..."}
    docs = find_documents(symbol, info.get("name") or symbol, info.get("website"))
    if not docs:
        yield {"type": "status", "text": "No report document found - analysing key figures only."}
    context, sources = build_context(info, fin, docs)
    yield {"type": "sources", "sources": sources}
    yield {"type": "status", "text": f"Analysing with {llm.load_config().model}..."}
    language = db.get_setting("ai_language", "English")
    out = []
    for chunk in llm.stream_chat([{"role": "user", "content": REPORT_TASK.format(context=context)}],
                                 system_prompt(lens, language)):
        out.append(chunk)
        yield {"type": "delta", "text": chunk}
    content = "".join(out)
    if content.strip():
        db.save_report(symbol, kind, datetime.now().isoformat(timespec="minutes"), content, json.dumps(sources))
    yield {"type": "done"}


def stream_answer(symbol: str, info: dict, fin: dict, question: str, history: list[dict]) -> Iterator[dict]:
    if not llm.load_config().configured:
        raise llm.LLMError("No LLM configured. Add a provider and API key under Settings.")
    docs = find_documents(symbol, info.get("name") or symbol, info.get("website"))
    context, sources = build_context(info, fin, docs)
    yield {"type": "sources", "sources": sources}
    language = db.get_setting("ai_language", "English")
    messages = [{"role": "user", "content": f"Reference material:\n{context}"},
                {"role": "assistant", "content": "Understood. I will answer based on this material."}]
    for m in history[-8:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": str(m["content"])[:4000]})
    messages.append({"role": "user", "content": question[:2000]})
    for chunk in llm.stream_chat(messages, system_prompt("general", language), max_tokens=1500):
        yield {"type": "delta", "text": chunk}
    yield {"type": "done"}
