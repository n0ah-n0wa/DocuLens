"""Security review regressions for ``AuthService``.

Covers the findings of the authentication review: refresh rotation must be atomic so a stolen
token cannot be redeemed in parallel with the legitimate one, a session must have an absolute
lifetime, concurrent hashing is bounded, and security events never carry credentials.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from doculens.application.auth import AuthConfig, AuthService, TokenType
from doculens.domain.auth import InvalidCredentialsError, InvalidTokenError, PasswordPolicy
from doculens.infrastructure.security.passwords import Argon2PasswordHasher
from doculens.infrastructure.security.tokens import JwtTokenCodec
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork

pytestmark = pytest.mark.unit

EMAIL = "alice@example.com"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value
SECRET = "security-test-secret-that-is-at-least-32-bytes"  # noqa: S105 - test-only value


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class SlowRotationUnitOfWork(InMemoryUnitOfWork):
    """Yields to the event loop between reading and rotating, like a real database round trip."""

    async def commit(self) -> None:
        await asyncio.sleep(0)
        await super().commit()


class CountingHasher(Argon2PasswordHasher):
    def __init__(self) -> None:
        super().__init__(time_cost=1, memory_cost=8192, parallelism=1)
        self.active = 0
        self.peak = 0

    def verify(self, password_hash: str, password: str) -> bool:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            return super().verify(password_hash, password)
        finally:
            self.active -= 1


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


def _service(
    store: InMemoryStore,
    clock: Clock,
    *,
    hasher: Argon2PasswordHasher | None = None,
    max_concurrent_hashes: int = 4,
) -> AuthService:
    return AuthService(
        unit_of_work=lambda: SlowRotationUnitOfWork(store),
        hasher=hasher or Argon2PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1),
        codec=JwtTokenCodec(secret=SECRET, issuer="doculens", audience="doculens-api", clock=clock),
        config=AuthConfig(
            access_token_ttl=timedelta(minutes=15),
            refresh_token_ttl=timedelta(days=30),
            password_policy=PasswordPolicy(),
            max_concurrent_hashes=max_concurrent_hashes,
        ),
        clock=clock,
    )


async def test_concurrent_refreshes_of_one_token_yield_at_most_one_session(
    store: InMemoryStore, clock: Clock
) -> None:
    service = _service(store, clock)
    await service.register(EMAIL, PASSWORD)
    pair = await service.login(EMAIL, PASSWORD)

    outcomes = await asyncio.gather(
        *(service.refresh(pair.refresh_token) for _ in range(5)), return_exceptions=True
    )

    successes = [o for o in outcomes if not isinstance(o, BaseException)]
    failures = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(successes) <= 1
    assert all(isinstance(f, InvalidTokenError) for f in failures)
    # A losing presentation is treated as reuse: nothing in the family is usable any more.
    assert all(token.is_consumed for token in store.refresh_tokens.values())
    for winner in successes:
        with pytest.raises(InvalidTokenError):
            await service.refresh(winner.refresh_token)


async def test_rotation_keeps_the_absolute_expiry_of_the_session(
    store: InMemoryStore, clock: Clock
) -> None:
    service = _service(store, clock)
    await service.register(EMAIL, PASSWORD)
    pair = await service.login(EMAIL, PASSWORD)
    session_expires_at = clock.now + timedelta(days=30)

    for _ in range(3):
        clock.advance(timedelta(days=9))
        pair = await service.refresh(pair.refresh_token)
        codec = JwtTokenCodec(
            secret=SECRET, issuer="doculens", audience="doculens-api", clock=clock
        )
        claims = codec.decode(pair.refresh_token, expected_type=TokenType.REFRESH)
        assert claims.expires_at == session_expires_at

    clock.advance(timedelta(days=9))  # 36 days after login: the session is over
    with pytest.raises(InvalidTokenError):
        await service.refresh(pair.refresh_token)


async def test_password_verification_concurrency_is_bounded(
    store: InMemoryStore, clock: Clock
) -> None:
    hasher = CountingHasher()
    service = _service(store, clock, hasher=hasher, max_concurrent_hashes=2)
    await service.register(EMAIL, PASSWORD)

    await asyncio.gather(*(service.login(EMAIL, PASSWORD) for _ in range(8)))

    assert hasher.peak <= 2


async def test_security_events_carry_identifiers_but_never_credentials(
    store: InMemoryStore, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    service = _service(store, clock)
    caplog.set_level(logging.INFO, logger="doculens.application.auth")

    user = await service.register(EMAIL, PASSWORD)
    pair = await service.login(EMAIL, PASSWORD)
    with pytest.raises(InvalidCredentialsError):
        await service.login(EMAIL, "wrong-password-value")
    rotated = await service.refresh(pair.refresh_token)
    with pytest.raises(InvalidTokenError):
        await service.refresh(pair.refresh_token)  # reuse
    await service.logout(rotated.refresh_token)

    operations = [getattr(r, "operation", None) for r in caplog.records]
    assert operations == [
        "auth.register",
        "auth.login",
        "auth.login_failed",
        "auth.token_reuse",
        "auth.logout",
    ]
    reuse = next(r for r in caplog.records if getattr(r, "operation", None) == "auth.token_reuse")
    assert reuse.levelno == logging.WARNING
    assert getattr(reuse, "user_id", None) == str(user.id)

    rendered = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (EMAIL, PASSWORD, "wrong-password-value", pair.refresh_token, pair.access_token):
        assert secret not in rendered
