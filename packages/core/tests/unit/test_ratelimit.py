"""Fixed-window limiter: budget per key, window reset, retry hints, no cross-key bleed."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from doculens.application.ratelimit import enforce
from doculens.domain.errors import RateLimitedError
from doculens.infrastructure.config import CoreSettings, QueueBackend, RateLimitBackend
from doculens.infrastructure.ratelimit import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    build_rate_limiter,
)

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeRedis:
    """Minimal async Redis surface for the shared rate limiter."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    async def expire(self, name: str, time: int) -> bool:
        if name not in self.values:
            return False
        self.ttls[name] = time
        return True

    async def ttl(self, name: str) -> int:
        if name not in self.values:
            return -2
        return self.ttls.get(name, -1)

    async def aclose(self) -> None:
        return None


async def test_budget_is_enforced_per_key_and_resets_after_the_window() -> None:
    clock = Clock()
    limiter = InMemoryRateLimiter(clock=clock)

    for _ in range(3):
        decision = await limiter.hit("a", limit=3, window_seconds=60)
        assert decision.allowed
    blocked = await limiter.hit("a", limit=3, window_seconds=60)
    assert not blocked.allowed
    assert blocked.retry_after_seconds == 60

    other = await limiter.hit("b", limit=3, window_seconds=60)
    assert other.allowed

    clock.advance(59.5)
    assert not (await limiter.hit("a", limit=3, window_seconds=60)).allowed
    clock.advance(0.5)
    assert (await limiter.hit("a", limit=3, window_seconds=60)).allowed


async def test_retry_after_counts_down_and_never_reports_zero() -> None:
    clock = Clock()
    limiter = InMemoryRateLimiter(clock=clock)
    await limiter.hit("k", limit=1, window_seconds=10)
    clock.advance(9.2)
    decision = await limiter.hit("k", limit=1, window_seconds=10)
    assert decision.retry_after_seconds == 1


async def test_enforce_raises_a_rate_limited_error_with_the_retry_hint() -> None:
    limiter = InMemoryRateLimiter(clock=Clock())
    await enforce(limiter, "k", limit=1, window_seconds=30)
    with pytest.raises(RateLimitedError) as excinfo:
        await enforce(limiter, "k", limit=1, window_seconds=30)
    assert excinfo.value.code == "RATE_LIMITED"
    assert excinfo.value.retry_after_seconds == 30


async def test_expired_windows_are_pruned_from_memory() -> None:
    clock = Clock()
    limiter = InMemoryRateLimiter(clock=clock)
    for index in range(1500):
        await limiter.hit(f"key-{index}", limit=10, window_seconds=1)
    clock.advance(2)
    for index in range(1100):
        await limiter.hit(f"fresh-{index}", limit=10, window_seconds=1)
    assert not any(key.startswith("key-") for key in limiter._windows)  # noqa: SLF001


async def test_redis_rate_limiter_enforces_a_shared_budget() -> None:
    clock = Clock()
    limiter = RedisRateLimiter(FakeRedis(), clock=clock)

    assert (await limiter.hit("user:1", limit=2, window_seconds=60)).allowed
    assert (await limiter.hit("user:1", limit=2, window_seconds=60)).allowed
    blocked = await limiter.hit("user:1", limit=2, window_seconds=60)
    assert not blocked.allowed
    assert blocked.retry_after_seconds == 60

    other = await limiter.hit("user:2", limit=2, window_seconds=60)
    assert other.allowed


async def test_redis_rate_limiter_rolls_to_the_next_bucket() -> None:
    clock = Clock()
    limiter = RedisRateLimiter(FakeRedis(), clock=clock)
    await limiter.hit("k", limit=1, window_seconds=10)
    assert not (await limiter.hit("k", limit=1, window_seconds=10)).allowed
    clock.advance(10)
    assert (await limiter.hit("k", limit=1, window_seconds=10)).allowed


def test_build_rate_limiter_uses_memory_when_requested() -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        rate_limit_backend=RateLimitBackend.MEMORY,
        queue_backend=QueueBackend.REDIS,
        redis_url="redis://127.0.0.1:6379/0",
    )
    assert isinstance(build_rate_limiter(settings), InMemoryRateLimiter)


def test_build_rate_limiter_auto_follows_redis_queue() -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        rate_limit_backend=RateLimitBackend.AUTO,
        queue_backend=QueueBackend.REDIS,
        redis_url="redis://127.0.0.1:6379/0",
    )
    limiter = build_rate_limiter(settings)
    assert isinstance(limiter, RedisRateLimiter)
    # from_url opens an asyncio client; close it so the suite never inherits a live pool.
    asyncio.run(limiter.close())


def test_build_rate_limiter_requires_redis_url_when_explicit() -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        rate_limit_backend=RateLimitBackend.REDIS,
        redis_url=None,
    )
    with pytest.raises(ValueError, match="REDIS_URL"):
        build_rate_limiter(settings)
