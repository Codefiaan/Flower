"""Kleiner In-Memory-TTL-Cache, damit Yahoo nicht bei jedem Klick erneut abgefragt wird."""
from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable

_store: dict[tuple, tuple[float, Any]] = {}
_lock = threading.Lock()


def ttl_cache(seconds: int) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (fn.__module__, fn.__qualname__, args, tuple(sorted(kwargs.items())))
            now = time.time()
            with _lock:
                hit = _store.get(key)
                if hit and hit[0] > now:
                    return hit[1]
            value = fn(*args, **kwargs)
            with _lock:
                _store[key] = (now + seconds, value)
            return value

        return wrapper

    return decorator


def clear() -> None:
    with _lock:
        _store.clear()
