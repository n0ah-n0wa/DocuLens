"""Authentication endpoints (SPECIFICATIONS.md §8, §33, §37).

Passwords arrive as ``SecretStr`` so they never appear in validation errors or reprs, are never
logged, and are handed to the application service only. Tokens are returned in the JSON body; how
the web client stores them is `OQ-19`.

Every endpoint is rate limited per client address before any credential work happens, which
bounds brute-force attempts on one account, credential stuffing across accounts, and Argon2 CPU
spend per client. Login is additionally limited per account (keyed by a hash of the normalised
email, so the limiter store never holds an address), which caps guesses against one account from
a distributed attacker. The limiter store is the in-process adapter until the Redis one lands.
"""

import hashlib
from http import HTTPStatus
from typing import Literal, Self

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, SecretStr

from doculens.application.auth import TokenPair
from doculens.application.ratelimit import RateLimiter, enforce
from doculens.domain.auth import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
    InvalidEmailError,
    normalize_email,
)
from doculens_api.dependencies import AuthServiceDep, ClientAddressDep, RateLimiterDep, SettingsDep
from doculens_api.errors import (
    BAD_REQUEST_RESPONSE,
    CONFLICT_RESPONSE,
    RATE_LIMITED_RESPONSE,
    ErrorResponse,
)
from doculens_api.routers.users import UserResponse
from doculens_api.settings import ApiSettings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

LOGIN_FAILURE_RESPONSES: dict[int | str, dict[str, object]] = {
    HTTPStatus.UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": "Unknown email or wrong password.",
    },
    HTTPStatus.FORBIDDEN: {
        "model": ErrorResponse,
        "description": "The account is suspended.",
    },
    **RATE_LIMITED_RESPONSE,
}

TOKEN_FAILURE_RESPONSES: dict[int | str, dict[str, object]] = {
    HTTPStatus.UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": "Invalid, expired, revoked or reused token.",
    },
    HTTPStatus.FORBIDDEN: {
        "model": ErrorResponse,
        "description": "The account is suspended.",
    },
    **RATE_LIMITED_RESPONSE,
}


class CredentialsRequest(BaseModel):
    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"]
    expires_in: int = Field(description="Access-token lifetime in seconds.")

    @classmethod
    def from_pair(cls, pair: TokenPair) -> Self:
        return cls(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            token_type="Bearer",  # noqa: S106 - a scheme name, not a credential
            expires_in=pair.expires_in,
        )


async def _throttle(limiter: RateLimiter, settings: ApiSettings, *keys: str) -> None:
    for key in keys:
        await enforce(
            limiter,
            key,
            limit=settings.auth_rate_limit_attempts,
            window_seconds=settings.auth_rate_limit_window_seconds,
        )


def _account_key(email: str) -> str:
    try:
        normalized = normalize_email(email)
    except InvalidEmailError:
        normalized = email.strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"auth:account:{digest}"


@router.post(
    "/register",
    status_code=HTTPStatus.CREATED,
    summary="Create an account",
    responses={
        **BAD_REQUEST_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_LIMITED_RESPONSE,
    },
)
async def register(
    body: CredentialsRequest,
    auth: AuthServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    client: ClientAddressDep,
) -> UserResponse:
    await _throttle(limiter, settings, f"auth:register:{client}")
    user = await auth.register(body.email, body.password.get_secret_value())
    return UserResponse.from_user(user)


@router.post(
    "/login",
    summary="Exchange credentials for tokens",
    responses=LOGIN_FAILURE_RESPONSES,
)
async def login(
    body: CredentialsRequest,
    auth: AuthServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    client: ClientAddressDep,
) -> TokenPairResponse:
    await _throttle(limiter, settings, f"auth:login:{client}", _account_key(body.email))
    pair = await auth.login(body.email, body.password.get_secret_value())
    return TokenPairResponse.from_pair(pair)


@router.post(
    "/refresh",
    summary="Rotate a refresh token",
    responses=TOKEN_FAILURE_RESPONSES,
)
async def refresh(
    body: RefreshRequest,
    auth: AuthServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    client: ClientAddressDep,
) -> TokenPairResponse:
    await _throttle(limiter, settings, f"auth:refresh:{client}")
    pair = await auth.refresh(body.refresh_token)
    return TokenPairResponse.from_pair(pair)


@router.post(
    "/logout",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Revoke the session of a refresh token",
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Malformed or expired token.",
        },
        **RATE_LIMITED_RESPONSE,
    },
)
async def logout(
    body: RefreshRequest,
    auth: AuthServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    client: ClientAddressDep,
) -> Response:
    await _throttle(limiter, settings, f"auth:logout:{client}")
    await auth.logout(body.refresh_token)
    return Response(status_code=HTTPStatus.NO_CONTENT)
