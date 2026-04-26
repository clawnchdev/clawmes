"""Sliding-window usage counter for rate-limit policies.

Tracks ``(user_id, tool_name)`` invocations over a 60-minute rolling
window. The counter is in-memory only — restarts reset it. Persistence
isn't needed because policies that fire after a restart's-worth of
quiet time are conservatively safe (they default-allow until usage
builds back up).

The data structure is a per-key deque of UTC timestamps. On ``record``,
old entries are evicted before the new one is appended. ``count`` does
the same eviction before returning the size.

Thread-safe via a single module-level RLock — counters are tiny so the
hot path doesn't suffer.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_WINDOW_SECONDS = 60 * 60  # 1 hour


class UsageCounter:
    """Per-(user, tool) sliding-window invocation counter."""

    def __init__(self, window_seconds: int = _WINDOW_SECONDS) -> None:
        self._window = window_seconds
        self._lock = threading.RLock()
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def record(self, user_id: str, tool_name: str) -> None:
        """Append a single invocation timestamp."""
        now = time.monotonic()
        key = (user_id, tool_name)
        with self._lock:
            bucket = self._buckets[key]
            self._evict(bucket, now)
            bucket.append(now)

    def count(self, user_id: str, tool_name: str) -> int:
        """Return the count within the last ``window_seconds``."""
        now = time.monotonic()
        key = (user_id, tool_name)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0
            self._evict(bucket, now)
            return len(bucket)

    def reset(self) -> None:
        """Clear all counters (test helper + ``/policy_clear``)."""
        with self._lock:
            self._buckets.clear()

    def _evict(self, bucket: deque[float], now: float) -> None:
        threshold = now - self._window
        while bucket and bucket[0] < threshold:
            bucket.popleft()


_instance: UsageCounter | None = None


def get_usage_counter() -> UsageCounter:
    global _instance
    if _instance is None:
        _instance = UsageCounter()
    return _instance
