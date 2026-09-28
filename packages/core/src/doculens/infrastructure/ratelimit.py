"""Fixed-window rate limiters (SPECIFICATIONS.md §37, §47).

``InMemoryRateLimiter`` is exact on a single process. ``RedisRateLimiter`` shares counters across
API instances when Redis is the configured store. The port is identical so callers do not change.
"""

import asyncio
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from redis.asyncio import Redis

from doculens.application.ratelimit import RateLimitDecision
from doculens.domain.time import utc_now
from doculens.infrastructure.config import CoreSettings, QueueBackend, RateLimitBackend

_PRUNE_EVERY = 1024
_KEY_PREFIX = "doculens:ratelimit"


class _RedisCommands(Protocol):
    async def incr(self, name: str) -> int: ...

    async def expire(self, name: str, time: int) -> bool: ...

    async def ttl(self, name: str) -> int: ...

    async def aclose(self) -> None: ...


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


class RedisRateLimiter:
    """Shared fixed-window limiter keyed by wall-clock buckets.

    Each ``(key, window)`` pair maps to one Redis counter with a TTL of ``window_seconds``. The
    first hit creates the key and sets expiry; later hits only ``INCR``. Retry-after uses the key
    TTL so clients wait until the bucket rolls over.
    """

    def __init__(
        self,
        client: _RedisCommands,
        *,
        key_prefix: str = _KEY_PREFIX,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._redis: Any = client
        self._prefix = key_prefix
        self._clock = clock

    @classmethod
    def from_url(cls, url: str, *, key_prefix: str = _KEY_PREFIX) -> "RedisRateLimiter":
        return cls(Redis.from_url(url, decode_responses=True), key_prefix=key_prefix)

    async def close(self) -> None:
        await self._redis.aclose()

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if window_seconds < 1:
            message = "window_seconds must be at least 1"
            raise ValueError(message)
        now = self._clock().timestamp()
        bucket = int(now // window_seconds)
        redis_key = f"{self._prefix}:{key}:{bucket}"
        count = int(await self._redis.incr(redis_key))
        if count == 1:
            await self._redis.expire(redis_key, window_seconds)
        if count > limit:
            ttl = int(await self._redis.ttl(redis_key))
            # TTL can be -1 (no expiry yet) or -2 (already gone) under races; never advertise 0.
            retry_after = ttl if ttl > 0 else max(1, math.ceil((bucket + 1) * window_seconds - now))
            return RateLimitDecision(allowed=False, retry_after_seconds=max(1, retry_after))
        return RateLimitDecision(allowed=True, retry_after_seconds=0)


def build_rate_limiter(settings: CoreSettings) -> InMemoryRateLimiter | RedisRateLimiter:
    """Choose the limiter store from settings.

    Redis is used when ``RATE_LIMIT_BACKEND=redis``, or automatically when the job queue already
    depends on Redis (same URL). Otherwise the in-process limiter is used. Deployed environments
    must set ``RATE_LIMIT_BACKEND=redis`` (validated in ``CoreSettings``).
    """
    use_redis = settings.rate_limit_backend is RateLimitBackend.REDIS or (
        settings.rate_limit_backend is RateLimitBackend.AUTO
        and settings.queue_backend is QueueBackend.REDIS
    )
    if not use_redis:
        return InMemoryRateLimiter()
    if settings.redis_url is None:
        message = "REDIS_URL is required when RATE_LIMIT_BACKEND=redis"
        raise ValueError(message)
    return RedisRateLimiter.from_url(settings.redis_url)


__all__ = [
    "InMemoryRateLimiter",
    "RedisRateLimiter",
    "build_rate_limiter",
]
