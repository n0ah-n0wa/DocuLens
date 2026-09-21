"""What every HTTP provider adapter shares: bounded retries and error-body summaries (§67).

Timeouts, connection errors and the retryable status codes are tried again with exponential
backoff and full jitter, honouring ``Retry-After`` up to the configured cap; the adapter
decides which structured error to raise once the attempts are exhausted.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from doculens.domain.time import utc_now

RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
HTTP_TOO_MANY_REQUESTS = 429
MAX_DIAGNOSTIC_CHARACTERS = 300

Sleeper = Callable[[float], Awaitable[None]]
Jitter = Callable[[], float]  # a number in [0, 1)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff: ``base * 2**attempt`` with full jitter, capped at ``max``."""

    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            message = "retry policy values must be non-negative and allow one attempt"
            raise ValueError(message)

    def delay(self, attempt: int, *, jitter: float, retry_after: float | None) -> float:
        """Seconds to wait before retry number ``attempt`` (1-based)."""
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay_seconds)
        ceiling = min(self.base_delay_seconds * (2.0 ** (attempt - 1)), self.max_delay_seconds)
        return ceiling * jitter


class RetryableError(Exception):
    """Internal to adapters: a failure the retry loop may try again."""

    def __init__(
        self,
        *,
        reason: str,
        cause: Exception | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cause = cause
        self.status_code = status_code
        self.retry_after = retry_after

    @property
    def rate_limited(self) -> bool:
        return self.status_code == HTTP_TOO_MANY_REQUESTS


async def run_with_retries[T](  # noqa: PLR0913 - the collaborators of one retry loop
    attempt_once: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy,
    sleep: Sleeper,
    jitter: Jitter,
    exhausted: Callable[[RetryableError, int], Exception],
    logger: logging.Logger,
    operation: str,
    extra: Mapping[str, object],
) -> T:
    """Call ``attempt_once(attempt)`` until it succeeds, raises something that is not a
    ``RetryableError``, or the policy is exhausted (then ``exhausted(failure, attempts)``)."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await attempt_once(attempt)
        except RetryableError as failure:
            if attempt >= policy.max_attempts:
                raise exhausted(failure, attempt) from failure.cause
            delay = policy.delay(attempt, jitter=jitter(), retry_after=failure.retry_after)
            logger.warning(
                "provider request will be retried",
                extra={
                    "operation": operation,
                    **extra,
                    "attempt": attempt,
                    "status": failure.status_code,
                    "delay_seconds": round(delay, 3),
                    "reason": failure.reason,
                },
            )
            await sleep(delay)


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - utc_now()).total_seconds())


def diagnostics_from(response: httpx.Response) -> str:
    """A bounded, key-free summary of an error body for server-side logs."""
    try:
        body = response.json()
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except ValueError:
        message = None
    text = message if isinstance(message, str) else response.text
    return f"http {response.status_code}: {text[:MAX_DIAGNOSTIC_CHARACTERS]}"


__all__ = [
    "HTTP_TOO_MANY_REQUESTS",
    "MAX_DIAGNOSTIC_CHARACTERS",
    "RETRYABLE_STATUS_CODES",
    "Jitter",
    "RetryPolicy",
    "RetryableError",
    "Sleeper",
    "diagnostics_from",
    "retry_after_seconds",
    "run_with_retries",
]
