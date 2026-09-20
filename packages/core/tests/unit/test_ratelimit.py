"""Fixed-window limiter: budget per key, window reset, retry hints, no cross-key bleed."""

from datetime import UTC, datetime, timedelta

import pytest

from doculens.application.ratelimit import enforce
from doculens.domain.errors import RateLimitedError
from doculens.infrastructure.ratelimit import InMemoryRateLimiter

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


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
