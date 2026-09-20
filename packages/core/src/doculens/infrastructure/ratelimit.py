"""In-process fixed-window rate limiter.

Limits are per process: on a single instance it is exact, on a fleet each instance keeps its own
counters. It is the interim implementation until the Redis adapter provides a shared store (§37,
§47); the port is identical so the swap does not touch callers.
"""

import asyncio
import math
from collections.abc import Callable
from datetime import datetime

from doculens.application.ratelimit import RateLimitDecision
from doculens.domain.time import utc_now

_PRUNE_EVERY = 1024


class InMemoryRateLimiter:
    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()
        self._hits_since_prune = 0

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        now = self._clock().timestamp()
        async with self._lock:
            started, count = self._windows.get(key, (now, 0))
            if now - started >= window_seconds:
                started, count = now, 0
            count += 1
            self._windows[key] = (started, count)
            self._maybe_prune(now, window_seconds)
        if count > limit:
            retry_after = math.ceil(started + window_seconds - now)
            return RateLimitDecision(allowed=False, retry_after_seconds=max(1, retry_after))
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    def _maybe_prune(self, now: float, window_seconds: int) -> None:
        self._hits_since_prune += 1
        if self._hits_since_prune < _PRUNE_EVERY:
            return
        self._hits_since_prune = 0
        expired = [
            k for k, (started, _) in self._windows.items() if now - started >= window_seconds
        ]
        for key in expired:
            del self._windows[key]
