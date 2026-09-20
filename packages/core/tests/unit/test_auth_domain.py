from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doculens.domain.auth import (
    InvalidEmailError,
    PasswordPolicy,
    PasswordPolicyError,
    RefreshToken,
    normalize_email,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Alice@Example.COM", "alice@example.com"), ("  bob@example.org ", "bob@example.org")],
)
def test_emails_are_normalised(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "no-at-sign", "two@@example.com", "a@b", "a b@c.d"])
def test_malformed_emails_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email(raw)


def test_password_policy_accepts_a_reasonable_password() -> None:
    PasswordPolicy().validate("correct horse battery", email="a@example.com")


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short", "at least 12"),
        ("x" * 129, "at most 128"),
        ("Alice@Example.com", "must not be the email"),
    ],
)
def test_password_policy_rejects_weak_passwords(password: str, fragment: str) -> None:
    with pytest.raises(PasswordPolicyError, match=fragment):
        PasswordPolicy().validate(password, email="alice@example.com")


def test_refresh_token_lifecycle() -> None:
    token = RefreshToken(
        id=uuid4(),
        user_id=uuid4(),
        family_id=uuid4(),
        issued_at=T0,
        expires_at=T0 + timedelta(days=30),
    )
    assert not token.is_consumed
    assert not token.is_expired(T0 + timedelta(days=29))
    assert token.is_expired(T0 + timedelta(days=30))

    successor = uuid4()
    rotated = token.rotate_to(successor, now=T0 + timedelta(minutes=1))
    assert rotated.is_consumed
    assert rotated.replaced_by_id == successor
    assert rotated.revoked_at == T0 + timedelta(minutes=1)

    revoked = token.revoke(now=T0 + timedelta(minutes=2))
    assert revoked.is_consumed
    assert revoked.revoke(now=T0 + timedelta(minutes=3)).revoked_at == T0 + timedelta(minutes=2)
