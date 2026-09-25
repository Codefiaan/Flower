from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import Provider


@lru_cache(maxsize=1)
def get_provider() -> Provider:
    if settings.demo:
        from .demo import DemoProvider

        return DemoProvider()
    from .yahoo import YahooProvider

    return YahooProvider()
