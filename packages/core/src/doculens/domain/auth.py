"""Authentication concepts (SPECIFICATIONS.md §8): credential policy, refresh tokens, errors.

Passwords never appear here in stored form; only their hashes travel through the domain. A refresh
token is a durable record keyed by the JWT ``jti`` claim and grouped into a *family* (one login
session). Rotation links each token to its successor; presenting a token that was already rotated
is treated as theft and the whole family is revoked (§8, token rotation and revocation).
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Self
from uuid import UUID

from doculens.domain.errors import (
    ConflictError,
    InvalidInputError,
    PermissionDeniedError,
    UnauthenticatedError,
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 320
DEFAULT_MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


class InvalidEmailError(InvalidInputError):
    code = "INVALID_EMAIL"
    default_message = "The email address is not valid."


class PasswordPolicyError(InvalidInputError):
    code = "PASSWORD_POLICY_VIOLATION"
    default_message = "The password does not meet the password policy."


class EmailAlreadyRegisteredError(ConflictError):
    code = "EMAIL_ALREADY_REGISTERED"
    default_message = "An account with this email address already exists."


class InvalidCredentialsError(UnauthenticatedError):
    code = "INVALID_CREDENTIALS"
    default_message = "The email address or password is incorrect."


class InvalidTokenError(UnauthenticatedError):
    code = "INVALID_TOKEN"
    default_message = "The token is invalid."


class TokenExpiredError(InvalidTokenError):
    code = "TOKEN_EXPIRED"
    default_message = "The token has expired."


class AccountSuspendedError(PermissionDeniedError):
    code = "ACCOUNT_SUSPENDED"
    default_message = "This account is suspended."


def normalize_email(raw: str) -> str:
    """Canonical form for storage and lookup: trimmed and lower-case; raises when malformed."""
    value = raw.strip().lower()
    if not value or len(value) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(value):
        raise InvalidEmailError
    return value


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    min_length: int = DEFAULT_MIN_PASSWORD_LENGTH
    max_length: int = MAX_PASSWORD_LENGTH

    def validate(self, password: str, *, email: str) -> None:
        if len(password) < self.min_length:
            message = f"The password must be at least {self.min_length} characters long."
            raise PasswordPolicyError(message)
        if len(password) > self.max_length:
            message = f"The password must be at most {self.max_length} characters long."
            raise PasswordPolicyError(message)
        if password.strip().lower() == email.strip().lower():
            message = "The password must not be the email address."
            raise PasswordPolicyError(message)


@dataclass(frozen=True, slots=True)
class RefreshToken:
    id: UUID
    user_id: UUID
    family_id: UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    replaced_by_id: UUID | None = None

    @property
    def is_consumed(self) -> bool:
        """Rotated or revoked: presenting it again is a reuse attempt."""
        return self.revoked_at is not None or self.replaced_by_id is not None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def rotate_to(self, successor_id: UUID, *, now: datetime) -> Self:
        return replace(self, replaced_by_id=successor_id, revoked_at=now)

    def revoke(self, *, now: datetime) -> Self:
        return self if self.revoked_at is not None else replace(self, revoked_at=now)
