"""Authentication use cases (SPECIFICATIONS.md §8, §9, §68).

``AuthService`` owns the rules: how accounts are created, which credentials are accepted, how
tokens are issued, rotated and revoked, and which account statuses may act. Hashing and token
encoding are ports so the rules are testable without Argon2 or JWT libraries. Password hashing
runs in a worker thread, bounded by a semaphore so a flood of logins cannot exhaust memory with
concurrent Argon2 instances.

Security events are logged through the standard library (routed into the structured pipeline)
with identifiers only: never an email, a password or a token.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from doculens.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from doculens.domain.auth import (
    AccountSuspendedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidTokenError,
    PasswordPolicy,
    RefreshToken,
    TokenExpiredError,
    normalize_email,
)
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.domain.users import User, UserStatus

logger = logging.getLogger(__name__)


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password_hash: str, password: str) -> bool: ...
    def needs_rehash(self, password_hash: str) -> bool: ...


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The claims §8 requires: subject, identifier, issued-at, expiration; issuer/audience are
    added and verified by the codec."""

    subject: UUID
    token_id: UUID
    token_type: TokenType
    issued_at: datetime
    expires_at: datetime
    family_id: UUID | None = None


class TokenCodec(Protocol):
    def encode(self, claims: TokenClaims) -> str: ...
    def decode(self, token: str, *, expected_type: TokenType) -> TokenClaims:
        """Verify signature, issuer, audience, expiry and type; raise ``InvalidTokenError`` or
        ``TokenExpiredError`` otherwise."""
        ...


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"  # noqa: S105 - a scheme name, not a credential


@dataclass(frozen=True, slots=True)
class AuthConfig:
    access_token_ttl: timedelta
    refresh_token_ttl: timedelta
    password_policy: PasswordPolicy
    max_concurrent_hashes: int = 4


Clock = Callable[[], datetime]

# Verified when the email is unknown so that a login attempt takes the same time either way.
_TIMING_PADDING_PASSWORD = "timing-padding-password-never-accepted"  # noqa: S105 - not a credential


class AuthService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        hasher: PasswordHasher,
        codec: TokenCodec,
        config: AuthConfig,
        clock: Clock = utc_now,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._hasher = hasher
        self._codec = codec
        self._config = config
        self._clock = clock
        self._hash_slots = asyncio.Semaphore(config.max_concurrent_hashes)
        self._padding_hash = hasher.hash(_TIMING_PADDING_PASSWORD)

    async def register(self, email: str, password: str) -> User:
        normalized = normalize_email(email)
        self._config.password_policy.validate(password, email=normalized)
        # Hash before the existence check so registration takes the same time either way.
        password_hash = await self._hash(password)
        now = self._clock()
        user = User(
            id=new_id(),
            email=normalized,
            password_hash=password_hash,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        async with self._unit_of_work() as uow:
            if await uow.users.get_by_email(normalized) is not None:
                raise EmailAlreadyRegisteredError
            await uow.users.add(user)
            await uow.commit()
        logger.info(
            "user registered", extra={"operation": "auth.register", "user_id": str(user.id)}
        )
        return user

    async def login(self, email: str, password: str) -> TokenPair:
        try:
            normalized = normalize_email(email)
        except InvalidEmailError as exc:
            self._log_login_failed("malformed_email")
            raise InvalidCredentialsError from exc
        async with self._unit_of_work() as uow:
            user = await uow.users.get_by_email(normalized)
            stored_hash = user.password_hash if user is not None else self._padding_hash
            verified = await self._verify(stored_hash, password)
            if user is None or not verified:
                self._log_login_failed("unknown_email" if user is None else "wrong_password")
                raise InvalidCredentialsError
            self._ensure_may_act(user)
            now = self._clock()
            if self._hasher.needs_rehash(user.password_hash):
                user = replace(user, password_hash=await self._hash(password))
            user = replace(user, last_login_at=now, updated_at=now)
            await uow.users.update(user)
            family_id = new_id()
            pair = await self._issue_pair(
                uow,
                user,
                family_id=family_id,
                now=now,
                expires_at=now + self._config.refresh_token_ttl,
            )
            await uow.commit()
        logger.info(
            "login succeeded",
            extra={"operation": "auth.login", "user_id": str(user.id), "family_id": str(family_id)},
        )
        return pair

    async def refresh(self, refresh_token: str) -> TokenPair:
        claims = self._codec.decode(refresh_token, expected_type=TokenType.REFRESH)
        now = self._clock()
        async with self._unit_of_work() as uow:
            record = await uow.refresh_tokens.get(claims.token_id)
            if record is None or record.user_id != claims.subject:
                raise InvalidTokenError
            if record.is_expired(now):
                raise TokenExpiredError
            successor_id = new_id()
            # Atomic compare-and-set: a concurrent or replayed presentation of the same token
            # loses here, which means the token leaked, so the whole session family ends.
            if not await uow.refresh_tokens.rotate(record.id, successor_id, now=now):
                revoked = await uow.refresh_tokens.revoke_family(record.family_id, now=now)
                await uow.commit()
                logger.warning(
                    "refresh token reuse detected; session family revoked",
                    extra={
                        "operation": "auth.token_reuse",
                        "user_id": str(record.user_id),
                        "family_id": str(record.family_id),
                        "revoked_tokens": revoked,
                    },
                )
                raise InvalidTokenError
            user = await uow.users.get(record.user_id)
            if user is None:
                raise InvalidTokenError
            self._ensure_may_act(user)
            # The successor inherits the family's absolute expiry so a session cannot be
            # extended indefinitely by refreshing before each expiry.
            pair = await self._issue_pair(
                uow,
                user,
                family_id=record.family_id,
                now=now,
                expires_at=record.expires_at,
                refresh_id=successor_id,
            )
            await uow.commit()
        return pair

    async def logout(self, refresh_token: str) -> None:
        """Revoke the session the token belongs to. Idempotent for unknown or consumed tokens."""
        claims = self._codec.decode(refresh_token, expected_type=TokenType.REFRESH)
        now = self._clock()
        async with self._unit_of_work() as uow:
            record = await uow.refresh_tokens.get(claims.token_id)
            if record is None or record.user_id != claims.subject:
                return
            revoked = await uow.refresh_tokens.revoke_family(record.family_id, now=now)
            await uow.commit()
        logger.info(
            "logout",
            extra={
                "operation": "auth.logout",
                "user_id": str(record.user_id),
                "family_id": str(record.family_id),
                "revoked_tokens": revoked,
            },
        )

    async def authenticate(self, access_token: str) -> User:
        claims = self._codec.decode(access_token, expected_type=TokenType.ACCESS)
        async with self._unit_of_work() as uow:
            user = await uow.users.get(claims.subject)
        if user is None:
            raise InvalidTokenError
        self._ensure_may_act(user)
        return user

    async def _hash(self, password: str) -> str:
        async with self._hash_slots:
            return await asyncio.to_thread(self._hasher.hash, password)

    async def _verify(self, password_hash: str, password: str) -> bool:
        async with self._hash_slots:
            return await asyncio.to_thread(self._hasher.verify, password_hash, password)

    @staticmethod
    def _log_login_failed(reason: str) -> None:
        logger.info("login failed", extra={"operation": "auth.login_failed", "reason": reason})

    @staticmethod
    def _ensure_may_act(user: User) -> None:
        if user.status is UserStatus.SUSPENDED:
            raise AccountSuspendedError
        if user.status is not UserStatus.ACTIVE:
            raise InvalidCredentialsError

    async def _issue_pair(
        self,
        uow: UnitOfWork,
        user: User,
        *,
        family_id: UUID,
        now: datetime,
        expires_at: datetime,
        refresh_id: UUID | None = None,
    ) -> TokenPair:
        refresh_record = RefreshToken(
            id=refresh_id if refresh_id is not None else new_id(),
            user_id=user.id,
            family_id=family_id,
            issued_at=now,
            expires_at=expires_at,
        )
        await uow.refresh_tokens.add(refresh_record)
        access = TokenClaims(
            subject=user.id,
            token_id=new_id(),
            token_type=TokenType.ACCESS,
            issued_at=now,
            expires_at=now + self._config.access_token_ttl,
        )
        refresh = TokenClaims(
            subject=user.id,
            token_id=refresh_record.id,
            token_type=TokenType.REFRESH,
            issued_at=now,
            expires_at=refresh_record.expires_at,
            family_id=family_id,
        )
        return TokenPair(
            access_token=self._codec.encode(access),
            refresh_token=self._codec.encode(refresh),
            expires_in=int(self._config.access_token_ttl.total_seconds()),
        )
