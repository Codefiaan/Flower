from __future__ import annotations

from functools import lru_cache

from .base import Provider


@lru_cache(maxsize=2)
def _provider(mode: str) -> Provider:
    if mode == "demo":
        from .demo import DemoProvider

        return DemoProvider()
    from .yahoo import YahooProvider

    return YahooProvider()


def get_provider() -> Provider:
    """The data provider for the data mode chosen on the Settings page (live Yahoo or demo)."""
    from .. import prefs

    return _provider("demo" if prefs.is_demo() else "live")
