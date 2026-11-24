"""In-process sliding-window rate limiter + concurrency guard.

Good for a single instance (the default deployment). For horizontal scaling, swap the storage for Redis
(same interface) - documented as a trade-off in the README.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request, WebSocket


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_s: float = 60.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False, round(window_s - (now - q[0]), 1)
        q.append(now)
        if len(self._hits) > 50_000:  # crude memory cap
            for k in [k for k, v in self._hits.items() if not v][:10_000]:
                self._hits.pop(k, None)
        return True, 0.0


class ConcurrencyGuard:
    def __init__(self) -> None:
        self._active: dict[str, int] = defaultdict(int)

    def acquire(self, key: str, limit: int) -> bool:
        if self._active[key] >= limit:
            return False
        self._active[key] += 1
        return True

    def release(self, key: str) -> None:
        self._active[key] = max(0, self._active[key] - 1)


def client_ip(conn: Request | WebSocket) -> str:
    forwarded = conn.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return conn.client.host if conn.client else "unknown"
