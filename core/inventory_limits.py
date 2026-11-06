"""Short-lived result cache and per-user rate limit for the inventory check (SAGOI-14).

Every live check is a billed Genie One question that takes 20 to 40 seconds, so repeat clicks for the
same product are served from a short cache, and each user is limited to a number of checks per minute.

Settings (environment variables, read on every call so they can be tuned without code changes):
  INVENTORY_CACHE_SECONDS            how long a successful result is reused (default 30; 0 turns the cache off)
  INVENTORY_RATE_LIMIT_PER_MINUTE    checks per signed-in user per minute (default 20; 0 turns the limit off)

Both live in this process's memory. That is enough for one app instance; if the app is ever scaled out to
several instances, each keeps its own cache and its own count.
"""
import math
import os
import threading
import time
from collections import OrderedDict, deque

DEFAULT_CACHE_SECONDS = 30.0
DEFAULT_RATE_LIMIT_PER_MINUTE = 20
RATE_WINDOW_SECONDS = 60.0


def _number_from_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return float(default)


def cache_seconds() -> float:
    return _number_from_env("INVENTORY_CACHE_SECONDS", DEFAULT_CACHE_SECONDS)


def rate_limit_per_minute() -> int:
    return int(_number_from_env("INVENTORY_RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT_PER_MINUTE))


class TtlCache:
    """A small thread-safe cache whose entries expire after a time to live."""

    def __init__(self, max_entries: int = 5000, clock=time.monotonic):
        self._items: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._clock = clock

    def get(self, key: str):
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._items[key]
                return None
            return value

    def set(self, key: str, value: dict, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            now = self._clock()
            self._items[key] = (now + ttl_seconds, value)
            self._items.move_to_end(key)
            if len(self._items) > self._max_entries:
                for k in [k for k, (exp, _) in self._items.items() if now >= exp]:
                    del self._items[k]
                while len(self._items) > self._max_entries:
                    self._items.popitem(last=False)          # drop the oldest

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class RateLimiter:
    """Allows each user a number of calls per sliding window (default: 20 per minute)."""

    def __init__(self, window_seconds: float = RATE_WINDOW_SECONDS, clock=time.monotonic):
        self._calls: "dict[str, deque]" = {}
        self._lock = threading.Lock()
        self._window = window_seconds
        self._clock = clock

    def allow(self, user_key: str, limit: int = None):
        """Return (allowed, retry_after_seconds). A refused call is not counted."""
        limit = rate_limit_per_minute() if limit is None else limit
        if limit <= 0:
            return True, 0
        with self._lock:
            now = self._clock()
            calls = self._calls.setdefault(user_key, deque())
            while calls and now - calls[0] >= self._window:
                calls.popleft()
            if len(calls) >= limit:
                return False, max(1, math.ceil(self._window - (now - calls[0])))
            calls.append(now)
            if len(self._calls) > 10000:                      # forget users with no recent calls
                for k in [k for k, v in self._calls.items() if not v or now - v[-1] >= self._window]:
                    del self._calls[k]
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._calls.clear()


result_cache = TtlCache()
rate_limiter = RateLimiter()


def reset() -> None:
    """Empty the cache and forget all rate-limit counts (used by tests)."""
    result_cache.clear()
    rate_limiter.clear()
