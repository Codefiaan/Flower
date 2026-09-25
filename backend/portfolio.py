"""Portfolio valuation: aggregates lots per symbol, converts to the base currency."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .providers.base import Provider


def fetch_infos(provider: Provider, symbols: list[str]) -> dict[str, dict]:
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(symbols, pool.map(provider.info, symbols)))


def _fx(provider: Provider, cur: str | None, base: str) -> float | None:
    try:
        return provider.fx(cur or base, base)
    except Exception:
        return None


def summarize(lots: list[dict], provider: Provider, base: str) -> dict:
    infos = fetch_infos(provider, [l["symbol"] for l in lots])
    grouped: dict[str, list[dict]] = defaultdict(list)
    for lot in lots:
        grouped[lot["symbol"]].append(lot)

    rows = []
    for sym, ls in grouped.items():
        info = infos.get(sym) or {}
        shares = sum(l["shares"] for l in ls)
        cost_local = sum(l["shares"] * l["buy_price"] for l in ls)
        price = info.get("price")
        rate = _fx(provider, info.get("currency"), base)
        value_local = shares * price if price is not None else None
        day_local = shares * info["change"] if info.get("change") is not None else None
        rows.append({
            "symbol": sym,
            "name": info.get("name") or sym,
            "currency": info.get("currency"),
            "sector": info.get("sector") or "Other",
            "shares": shares,
            "avg_price": cost_local / shares if shares else None,
            "price": price,
            "change_pct": info.get("change_pct"),
            "pe": info.get("pe"),
            "div_yield": info.get("div_yield"),
            "market_cap": info.get("market_cap"),
            "high52": info.get("high52"),
            "low52": info.get("low52"),
            "value": value_local * rate if value_local is not None and rate else None,
            "cost": cost_local * rate if rate else None,
            "day_change": day_local * rate if day_local is not None and rate else None,
            "pl": (value_local - cost_local) * rate if value_local is not None and rate else None,
            "pl_pct": (value_local / cost_local - 1) * 100 if value_local is not None and cost_local else None,
            "lots": ls,
        })

    total_value = sum(r["value"] or 0 for r in rows)
    total_cost = sum(r["cost"] or 0 for r in rows)
    total_day = sum(r["day_change"] or 0 for r in rows)
    for r in rows:
        r["weight"] = (r["value"] / total_value * 100) if r["value"] and total_value else None
    rows.sort(key=lambda r: -(r["value"] or 0))

    def alloc(key: str) -> list[dict]:
        acc: dict[str, float] = defaultdict(float)
        for r in rows:
            acc[r[key] or "Other"] += r["value"] or 0
        return [{"label": k, "value": v, "pct": v / total_value * 100 if total_value else 0}
                for k, v in sorted(acc.items(), key=lambda kv: -kv[1])]

    prev_value = total_value - total_day
    return {
        "base_currency": base,
        "positions": rows,
        "total_value": total_value,
        "total_cost": total_cost,
        "total_pl": total_value - total_cost,
        "total_pl_pct": (total_value / total_cost - 1) * 100 if total_cost else None,
        "day_change": total_day,
        "day_change_pct": (total_day / prev_value * 100) if prev_value else None,
        "by_sector": alloc("sector"),
        "by_currency": alloc("currency"),
    }


def value_history(lots: list[dict], provider: Provider, base: str, period: str = "1y") -> list[dict]:
    """Portfolio value and invested capital over time, replayed from each lot's buy date.

    Lots without a buy date count as held for the whole period. FX uses today's rate
    (a simplification; historic FX would need an extra series per currency).
    `cost` lets the UI separate market gains from new money put in.
    """
    if not lots:
        return []
    values, costs = [], []
    for sym in dict.fromkeys(l["symbol"] for l in lots):
        h = provider.history(sym, period)
        if h.empty:
            continue
        info = provider.info(sym)
        rate = _fx(provider, info.get("currency"), base) or 0
        held = pd.Series(0.0, index=h.index)
        cost = pd.Series(0.0, index=h.index)
        for l in (x for x in lots if x["symbol"] == sym):
            start = pd.Timestamp(l["buy_date"]) if l.get("buy_date") else h.index[0]
            mask = h.index >= start
            held[mask] += l["shares"]
            cost[mask] += l["shares"] * l["buy_price"] * rate
        values.append((h["Close"] * held * rate).rename(sym))
        costs.append(cost.rename(sym))
    if not values:
        return []
    val = pd.concat(values, axis=1).sort_index().ffill().fillna(0).sum(axis=1)
    cst = pd.concat(costs, axis=1).sort_index().ffill().fillna(0).sum(axis=1)
    keep = val > 0
    intraday = period in ("1d", "5d")
    return [{"time": int(ts.timestamp()) if intraday else ts.strftime("%Y-%m-%d"), "value": float(v), "cost": float(cst[ts])}
            for ts, v in val[keep].items()]
