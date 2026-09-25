"""Screener presets. The actual filtering is done by the provider (Yahoo's screener)."""
from __future__ import annotations

PRESETS = {
    "value": {"label": "Value: P/E below 15", "pe_min": 0.1, "pe_max": 15, "mcap_min": 2e9},
    "dividend": {"label": "Dividend yield above 3%", "div_min": 0.03, "mcap_min": 2e9},
    "quality": {"label": "Quality: ROE above 15%, P/E below 30", "roe_min": 0.15, "pe_min": 0.1, "pe_max": 30, "mcap_min": 1e10},
    "largecap_cheap": {"label": "Large caps with P/E below 20", "pe_min": 0.1, "pe_max": 20, "mcap_min": 5e10},
}

REGIONS = {"": "All regions", "us": "USA", "de": "Germany", "eu": "Europe", "gb": "United Kingdom",
           "fr": "France", "ch": "Switzerland", "nl": "Netherlands", "jp": "Japan"}

SECTORS = ["Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy",
           "Financial Services", "Healthcare", "Industrials", "Real Estate", "Technology", "Utilities"]

FILTER_KEYS = ("region", "sector", "pe_min", "pe_max", "div_min", "roe_min", "mcap_min")


def build_filters(preset: str | None, overrides: dict) -> dict:
    f = {k: v for k, v in PRESETS.get(preset or "", {}).items() if k != "label"}
    for k in FILTER_KEYS:
        if overrides.get(k) not in (None, ""):
            f[k] = overrides[k]
    return f
