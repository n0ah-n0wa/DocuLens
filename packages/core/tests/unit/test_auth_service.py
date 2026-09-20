"""AuthService rules with the in-memory unit of work, a fast Argon2id and a controllable clock."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from doculens.application.auth import AuthConfig, AuthService, TokenPair, TokenType
from doculens.domain.auth import (
    AccountSuspendedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidTokenError,
    PasswordPolicy,
    PasswordPolicyError,
    TokenExpiredError,
)
from doculens.domain.users import UserStatus
from doculens.infrastructure.security.passwords import Argon2PasswordHasher
from doculens.infrastructure.security.tokens import JwtTokenCodec
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-that-is-at-least-32-bytes"  # noqa: S105 - test-only value
PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture(scope="module")
def hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)


@pytest.fixture
def codec(clock: Clock) -> JwtTokenCodec:
    return JwtTokenCodec(secret=SECRET, issuer="doculens", audience="doculens-api", clock=clock)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def service(
    store: InMemoryStore, hasher: Argon2PasswordHasher, codec: JwtTokenCodec, clock: Clock
) -> AuthService:
    return AuthService(
        unit_of_work=lambda: InMemoryUnitOfWork(store),
        hasher=hasher,
        codec=codec,
        config=AuthConfig(
            access_token_ttl=ACCESS_TTL,
            refresh_token_ttl=REFRESH_TTL,
            password_policy=PasswordPolicy(),
        ),
        clock=clock,
    )


async def _registered_login(service: AuthService) -> TokenPair:
    await service.register("Alice@Example.com", PASSWORD)
    return await service.login("alice@example.com", PASSWORD)


async def test_register_normalises_the_email_and_stores_only_a_hash(
    service: AuthService, store: InMemoryStore
) -> None:
    user = await service.register("  Alice@Example.COM ", PASSWORD)

    assert user.email == "alice@example.com"
    assert user.status is UserStatus.ACTIVE
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert store.users[user.id] == user
    assert store.commits == 1


async def test_duplicate_registration_is_a_conflict(service: AuthService) -> None:
    await service.register("alice@example.com", PASSWORD)

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register("ALICE@example.com", PASSWORD)


async def test_registration_validates_email_and_password(service: AuthService) -> None:
    with pytest.raises(InvalidEmailError):
        await service.register("not-an-email", PASSWORD)
    with pytest.raises(PasswordPolicyError):
        await service.register("alice@example.com", "short")


async def test_login_issues_a_token_pair_and_records_the_login(
    service: AuthService, store: InMemoryStore, codec: JwtTokenCodec, clock: Clock
) -> None:
    pair = await _registered_login(service)

    access = codec.decode(pair.access_token, expected_type=TokenType.ACCESS)
    refresh = codec.decode(pair.refresh_token, expected_type=TokenType.REFRESH)
    (user,) = store.users.values()
    assert pair.token_type == "Bearer"  # noqa: S105 - a scheme name, not a credential
    assert pair.expires_in == int(ACCESS_TTL.total_seconds())
    assert access.subject == user.id
    assert access.expires_at == clock.now + ACCESS_TTL
    assert refresh.expires_at == clock.now + REFRESH_TTL
    assert refresh.token_id in store.refresh_tokens
    assert refresh.family_id == store.refresh_tokens[refresh.token_id].family_id
    assert user.last_login_at == clock.now


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("alice@example.com", "wrong password entirely"),
        ("nobody@example.com", PASSWORD),
        ("not an email", PASSWORD),
    ],
)
async def test_wrong_credentials_are_rejected_uniformly(
    service: AuthService, email: str, password: str
) -> None:
    await service.register("alice@example.com", PASSWORD)

    with pytest.raises(InvalidCredentialsError):
        await service.login(email, password)


async def test_suspended_and_deleted_accounts_cannot_log_in(
    service: AuthService, store: InMemoryStore
) -> None:
    user = await service.register("alice@example.com", PASSWORD)

    store.users[user.id] = replace(user, status=UserStatus.SUSPENDED)
    with pytest.raises(AccountSuspendedError):
        await service.login("alice@example.com", PASSWORD)

    store.users[user.id] = replace(user, status=UserStatus.DELETED)
    with pytest.raises(InvalidCredentialsError):
        await service.login("alice@example.com", PASSWORD)


async def test_refresh_rotates_tokens_and_detects_reuse(
    service: AuthService, store: InMemoryStore, codec: JwtTokenCodec
) -> None:
    first = await _registered_login(service)

    second = await service.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    assert second.access_token != first.access_token
    old = codec.decode(first.refresh_token, expected_type=TokenType.REFRESH)
    new = codec.decode(second.refresh_token, expected_type=TokenType.REFRESH)
    assert store.refresh_tokens[old.token_id].replaced_by_id == new.token_id
    assert new.family_id == old.family_id

    # Reusing the rotated token is treated as theft: the whole family dies, including the new one.
    with pytest.raises(InvalidTokenError):
        await service.refresh(first.refresh_token)
    with pytest.raises(InvalidTokenError):
        await service.refresh(second.refresh_token)


async def test_expired_refresh_tokens_are_rejected(service: AuthService, clock: Clock) -> None:
    pair = await _registered_login(service)
    clock.advance(REFRESH_TTL + timedelta(seconds=1))

    with pytest.raises(TokenExpiredError):
        await service.refresh(pair.refresh_token)


async def test_logout_revokes_the_session_and_is_idempotent(service: AuthService) -> None:
    pair = await _registered_login(service)

    await service.logout(pair.refresh_token)
    await service.logout(pair.refresh_token)

    with pytest.raises(InvalidTokenError):
        await service.refresh(pair.refresh_token)


async def test_access_tokens_authenticate_until_they_expire(
    service: AuthService, clock: Clock
) -> None:
    pair = await _registered_login(service)

    user = await service.authenticate(pair.access_token)
    assert user.email == "alice@example.com"

    clock.advance(ACCESS_TTL + timedelta(seconds=1))
    with pytest.raises(TokenExpiredError):
        await service.authenticate(pair.access_token)


async def test_a_refresh_token_cannot_be_used_as_an_access_token(service: AuthService) -> None:
    pair = await _registered_login(service)

    with pytest.raises(InvalidTokenError):
        await service.authenticate(pair.refresh_token)
    with pytest.raises(InvalidTokenError):
        await service.refresh(pair.access_token)


async def test_malformed_tokens_are_rejected(service: AuthService) -> None:
    with pytest.raises(InvalidTokenError):
        await service.authenticate("definitely.not.a.jwt")
    with pytest.raises(InvalidTokenError):
        await service.refresh("")


async def test_a_suspended_user_is_refused_even_with_a_valid_token(
    service: AuthService, store: InMemoryStore
) -> None:
    pair = await _registered_login(service)
    (user,) = store.users.values()
    store.users[user.id] = replace(user, status=UserStatus.SUSPENDED)

    with pytest.raises(AccountSuspendedError):
        await service.authenticate(pair.access_token)
    with pytest.raises(AccountSuspendedError):
        await service.refresh(pair.refresh_token)


async def test_tokens_of_a_deleted_user_are_invalid(
    service: AuthService, store: InMemoryStore
) -> None:
    pair = await _registered_login(service)
    store.users.clear()

    with pytest.raises(InvalidTokenError):
        await service.authenticate(pair.access_token)
    with pytest.raises(InvalidTokenError):
        await service.refresh(pair.refresh_token)


async def test_login_rehashes_passwords_stored_with_weaker_parameters(
    store: InMemoryStore, codec: JwtTokenCodec, clock: Clock
) -> None:
    weak = Argon2PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    strong = Argon2PasswordHasher(time_cost=2, memory_cost=8192, parallelism=1)
    config = AuthConfig(
        access_token_ttl=ACCESS_TTL, refresh_token_ttl=REFRESH_TTL, password_policy=PasswordPolicy()
    )
    registered_with_weak = AuthService(
        unit_of_work=lambda: InMemoryUnitOfWork(store), hasher=weak, codec=codec, config=config
    )
    user = await registered_with_weak.register("alice@example.com", PASSWORD)

    upgraded = AuthService(
        unit_of_work=lambda: InMemoryUnitOfWork(store),
        hasher=strong,
        codec=codec,
        config=config,
        clock=clock,
    )
    await upgraded.login("alice@example.com", PASSWORD)

    assert store.users[user.id].password_hash != user.password_hash
    assert strong.needs_rehash(store.users[user.id].password_hash) is False
