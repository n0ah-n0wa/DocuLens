"""Rate-limiting port (SPECIFICATIONS.md §37).

Use cases and routes ask the limiter to record one hit for a key and learn whether the budget for
the current window is exhausted. The key is chosen by the caller (client address, a hashed email,
a user ID) so that no personal data reaches the limiter store.
"""

from dataclasses import dataclass
from typing import Protocol

from doculens.domain.errors import RateLimitedError


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision: ...


async def enforce(limiter: RateLimiter, key: str, *, limit: int, window_seconds: int) -> None:
    """Record a hit and raise ``RateLimitedError`` once the window budget is spent."""
    decision = await limiter.hit(key, limit=limit, window_seconds=window_seconds)
    if not decision.allowed:
        raise RateLimitedError(decision.retry_after_seconds)
