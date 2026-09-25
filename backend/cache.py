"""Small in-memory TTL cache so Yahoo isn't queried again on every click."""
from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable

_store: dict[tuple, tuple[float, Any]] = {}
_lock = threading.Lock()


class Uncached:
    """Return `uncached(value)` from a cached function to hand back `value` without storing it,
    e.g. after a failed request, so a temporary outage or rate limit isn't remembered."""

    __slots__ = ("value",)

    def __init__(self, value: Any):
        self.value = value


def uncached(value: Any) -> Uncached:
    return Uncached(value)


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
            if isinstance(value, Uncached):
                return value.value
            with _lock:
                _store[key] = (now + seconds, value)
            return value

        return wrapper

    return decorator


def clear() -> None:
    with _lock:
        _store.clear()
