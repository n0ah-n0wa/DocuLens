from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from doculens.application.auth import TokenClaims, TokenType
from doculens.domain.auth import InvalidTokenError, TokenExpiredError
from doculens.infrastructure.security.tokens import JwtTokenCodec

pytestmark = pytest.mark.unit

SECRET = "unit-test-secret-that-is-at-least-32-bytes"  # noqa: S105 - test-only value
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def codec() -> JwtTokenCodec:
    return JwtTokenCodec(secret=SECRET, issuer="doculens", audience="doculens-api")


def _claims(
    token_type: TokenType = TokenType.ACCESS, *, expires_at: datetime | None = None
) -> TokenClaims:
    return TokenClaims(
        subject=uuid4(),
        token_id=uuid4(),
        token_type=token_type,
        issued_at=NOW,
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=15),
        family_id=uuid4() if token_type is TokenType.REFRESH else None,
    )


def test_round_trip_preserves_every_claim(codec: JwtTokenCodec) -> None:
    claims = _claims(TokenType.REFRESH, expires_at=datetime.now(UTC) + timedelta(days=1))

    decoded = codec.decode(codec.encode(claims), expected_type=TokenType.REFRESH)

    assert decoded.subject == claims.subject
    assert decoded.token_id == claims.token_id
    assert decoded.family_id == claims.family_id
    assert decoded.token_type is TokenType.REFRESH
    assert decoded.issued_at == claims.issued_at
    assert decoded.expires_at == claims.expires_at.replace(microsecond=0)


def test_tokens_carry_issuer_audience_and_type(codec: JwtTokenCodec) -> None:
    payload = jwt.decode(codec.encode(_claims()), options={"verify_signature": False})

    assert payload["iss"] == "doculens"
    assert payload["aud"] == "doculens-api"
    assert payload["typ"] == "access"
    assert set(payload) >= {"sub", "jti", "iat", "exp"}


def test_expired_tokens_are_reported_as_expired(codec: JwtTokenCodec) -> None:
    token = codec.encode(_claims(expires_at=datetime.now(UTC) - timedelta(seconds=1)))

    with pytest.raises(TokenExpiredError):
        codec.decode(token, expected_type=TokenType.ACCESS)


def test_wrong_issuer_or_audience_is_rejected(codec: JwtTokenCodec) -> None:
    other_issuer = JwtTokenCodec(secret=SECRET, issuer="someone-else", audience="doculens-api")
    other_audience = JwtTokenCodec(secret=SECRET, issuer="doculens", audience="other-api")

    for foreign in (other_issuer, other_audience):
        with pytest.raises(InvalidTokenError):
            codec.decode(foreign.encode(_claims()), expected_type=TokenType.ACCESS)


def test_type_mismatch_is_rejected(codec: JwtTokenCodec) -> None:
    refresh = codec.encode(_claims(TokenType.REFRESH))

    with pytest.raises(InvalidTokenError):
        codec.decode(refresh, expected_type=TokenType.ACCESS)


@pytest.mark.parametrize("malformed", ["", "not.a.jwt", "a.b", "eyJ.eyJ.sig"])
def test_malformed_tokens_are_rejected(codec: JwtTokenCodec, malformed: str) -> None:
    with pytest.raises(InvalidTokenError):
        codec.decode(malformed, expected_type=TokenType.ACCESS)


def test_tampered_signature_and_wrong_secret_are_rejected(codec: JwtTokenCodec) -> None:
    token = codec.encode(_claims())
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}AA"
    other_secret = JwtTokenCodec(
        secret="another-secret-that-is-also-32-bytes-long",  # noqa: S106 - test-only
        issuer="doculens",
        audience="doculens-api",
    ).encode(_claims())

    for bad in (tampered, other_secret):
        with pytest.raises(InvalidTokenError):
            codec.decode(bad, expected_type=TokenType.ACCESS)


def test_unsigned_and_incomplete_tokens_are_rejected(codec: JwtTokenCodec) -> None:
    unsigned = jwt.encode(
        {"iss": "doculens", "aud": "doculens-api", "sub": str(uuid4()), "typ": "access"},
        key=None,
        algorithm="none",
    )
    missing_jti = jwt.encode(
        {
            "iss": "doculens",
            "aud": "doculens-api",
            "sub": str(uuid4()),
            "typ": "access",
            "iat": int(NOW.timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    bad_subject = jwt.encode(
        {
            "iss": "doculens",
            "aud": "doculens-api",
            "sub": "not-a-uuid",
            "jti": str(uuid4()),
            "typ": "access",
            "iat": int(NOW.timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )

    for bad in (unsigned, missing_jti, bad_subject):
        with pytest.raises(InvalidTokenError):
            codec.decode(bad, expected_type=TokenType.ACCESS)
