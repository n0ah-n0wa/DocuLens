"""JWT encoding and verification (SPECIFICATIONS.md §8) via PyJWT.

Tokens are HS256-signed with the process secret and carry ``iss``, ``aud``, ``sub``, ``jti``,
``iat``, ``exp`` and ``typ`` (access or refresh). Decoding requires every one of those claims and
verifies signature, issuer, audience, expiry and type; anything else is an invalid token.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from doculens.application.auth import Clock, TokenClaims, TokenType
from doculens.domain.auth import InvalidTokenError, TokenExpiredError
from doculens.domain.time import utc_now

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "jti", "iat", "exp", "typ")


class JwtTokenCodec:
    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        leeway_seconds: int = 0,
        clock: Clock = utc_now,
    ) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._leeway = timedelta(seconds=leeway_seconds)
        self._clock = clock

    def encode(self, claims: TokenClaims) -> str:
        payload: dict[str, str | int] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(claims.subject),
            "jti": str(claims.token_id),
            "typ": claims.token_type.value,
            "iat": int(claims.issued_at.timestamp()),
            "exp": int(claims.expires_at.timestamp()),
        }
        if claims.family_id is not None:
            payload["fam"] = str(claims.family_id)
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM)

    def decode(self, token: str, *, expected_type: TokenType) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                # Expiry is checked below against the injected clock, not the wall clock.
                options={"require": list(REQUIRED_CLAIMS), "verify_exp": False},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError from exc
        if payload.get("typ") != expected_type.value:
            raise InvalidTokenError
        try:
            family = payload.get("fam")
            claims = TokenClaims(
                subject=UUID(str(payload["sub"])),
                token_id=UUID(str(payload["jti"])),
                token_type=expected_type,
                issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
                expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
                family_id=UUID(str(family)) if family is not None else None,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise InvalidTokenError from exc
        if self._clock() >= claims.expires_at + self._leeway:
            raise TokenExpiredError
        return claims
