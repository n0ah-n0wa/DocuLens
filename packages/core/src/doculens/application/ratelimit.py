"""Rate-limiting port and key policy (SPECIFICATIONS.md §37).

Use cases and routes ask the limiter to record one hit for a key and learn whether the budget for
the current window is exhausted. Keys are built here so callers share one strategy:

- authentication: per client address, plus per hashed account on login;
- authenticated expensive work: per user **and** per client address on short windows (so many
  accounts behind one IP cannot trivially bypass a user budget), per user only on daily quotas;
- no personal data (raw emails, tokens) reaches the limiter store.

Proxy forwarding headers are intentionally ignored at the edge of the API until OQ-19 fixes which
proxy sits in front; callers must pass the peer address from ``request.client``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from doculens.domain.auth import InvalidEmailError, normalize_email
from doculens.domain.errors import RateLimitedError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

# Reject composite / spoof-shaped peer strings; never split on commas as if they were XFF.
_SAFE_CLIENT = re.compile(r"^[0-9a-fA-F:.%/-]{1,128}$|^[A-Za-z0-9._-]{1,128}$")


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


async def enforce_keys(
    limiter: RateLimiter,
    keys: Sequence[str],
    *,
    limit: int,
    window_seconds: int,
) -> None:
    """Apply the same budget to every key; the first exhausted key wins."""
    for key in keys:
        await enforce(limiter, key, limit=limit, window_seconds=window_seconds)


def normalize_client_address(client: str | None) -> str:
    """Stable peer identity for limiter keys; never parse ``X-Forwarded-For`` here."""
    if client is None:
        return "unknown"
    text = client.strip().lower()
    if not text or not _SAFE_CLIENT.match(text) or "," in text:
        return "unknown"
    return text


def auth_address_key(action: str, client: str | None) -> str:
    """Per-address auth budget (register/login/refresh/logout)."""
    return f"auth:{action}:{normalize_client_address(client)}"


def auth_account_key(email: str) -> str:
    """Per-account login budget; email is normalised then hashed so the store never holds it."""
    try:
        normalized = normalize_email(email)
    except InvalidEmailError:
        normalized = email.strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"auth:account:{digest}"


def user_key(bucket: str, user_id: UUID | str) -> str:
    return f"{bucket}:user:{user_id}"


def ip_key(bucket: str, client: str | None) -> str:
    return f"{bucket}:ip:{normalize_client_address(client)}"


def user_and_ip_keys(bucket: str, *, user_id: UUID | str, client: str | None) -> tuple[str, str]:
    """Short-window keys for authenticated expensive endpoints (§37)."""
    return (user_key(bucket, user_id), ip_key(bucket, client))


def user_only_keys(bucket: str, *, user_id: UUID | str) -> tuple[str, ...]:
    """Daily / spend quotas stay per-user so shared NATs are not starved."""
    return (user_key(bucket, user_id),)


__all__ = [
    "RateLimitDecision",
    "RateLimiter",
    "auth_account_key",
    "auth_address_key",
    "enforce",
    "enforce_keys",
    "ip_key",
    "normalize_client_address",
    "user_and_ip_keys",
    "user_key",
    "user_only_keys",
]
