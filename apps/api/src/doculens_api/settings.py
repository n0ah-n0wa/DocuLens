"""Settings owned by the API process, on top of the core settings."""

from __future__ import annotations

import math
from collections import Counter
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator

from doculens.infrastructure.config import CoreSettings

MIN_JWT_SECRET_LENGTH = 32
MIN_JWT_SECRET_UNIQUE_CHARS = 10
MIN_JWT_SECRET_ENTROPY_BITS = 3.0
_FORBIDDEN_JWT_SECRETS = frozenset(
    {
        "replace-with-a-random-string-of-at-least-32-characters",
        "change-me-change-me-change-me-change-me",
        "your-secret-key-must-be-at-least-32-chars",
    }
)


def jwt_secret_entropy_bits(secret: str) -> float:
    """Shannon entropy in bits per character; used to reject low-diversity signing keys."""
    length = len(secret)
    if length == 0:
        return 0.0
    counts = Counter(secret)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


class ApiSettings(CoreSettings):
    api_docs_enabled: bool | None = Field(
        default=None,
        description=(
            "Serve the OpenAPI document and interactive docs (§75). Unset means enabled locally "
            "and disabled in deployed environments; set explicitly to override."
        ),
    )
    request_id_header: str = Field(
        default="X-Request-ID",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9-]*$",
        description="Header used to receive and return the request correlation ID (§36).",
    )
    health_probe_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30,
        description="Upper bound for each dependency probe run by /health/ready.",
    )

    jwt_secret: SecretStr = Field(
        min_length=MIN_JWT_SECRET_LENGTH,
        description="HS256 signing key for access and refresh tokens (§8, §54).",
    )
    jwt_issuer: str = Field(default="doculens", min_length=1, max_length=100)
    jwt_audience: str = Field(default="doculens-api", min_length=1, max_length=100)
    access_token_ttl_seconds: int = Field(
        default=900, ge=60, le=3600, description="Short-lived access tokens (§8)."
    )
    refresh_token_ttl_seconds: int = Field(
        default=30 * 24 * 3600, ge=3600, le=90 * 24 * 3600, description="Refresh lifetime (§8)."
    )
    password_min_length: int = Field(default=12, ge=8, le=128)
    auth_rate_limit_attempts: int = Field(
        default=10, ge=1, le=1000, description="Auth requests allowed per key per window (§37)."
    )
    auth_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    upload_rate_limit_attempts: int = Field(
        default=30,
        ge=1,
        le=10_000,
        description="Document uploads allowed per authenticated user per window (§37).",
    )
    upload_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    ask_rate_limit_attempts: int = Field(
        default=30,
        ge=1,
        le=10_000,
        description="Questions allowed per authenticated user per short window (§37).",
    )
    ask_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    ask_daily_rate_limit_attempts: int = Field(
        default=500,
        ge=1,
        le=1_000_000,
        description=(
            "Questions allowed per authenticated user per day (§37, §39). "
            "Bounds AI spend beyond the short-window burst limit."
        ),
    )
    ask_daily_rate_limit_window_seconds: int = Field(
        default=86_400,
        ge=60,
        le=604_800,
        description="Daily ask quota window in seconds (default 24h).",
    )
    ai_ops_rate_limit_attempts: int = Field(
        default=30,
        ge=1,
        le=10_000,
        description=(
            "Document reprocess/reindex attempts per authenticated user per short window (§37)."
        ),
    )
    ai_ops_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    ai_ops_daily_rate_limit_attempts: int = Field(
        default=200,
        ge=1,
        le=1_000_000,
        description=(
            "Document reprocess/reindex attempts per authenticated user per day (§37, §39)."
        ),
    )
    ai_ops_daily_rate_limit_window_seconds: int = Field(
        default=86_400,
        ge=60,
        le=604_800,
        description="Daily AI-ops quota window in seconds (default 24h).",
    )
    cors_origins: str = Field(
        default="",
        description=(
            "Comma-separated browser origins allowed for CORS (OQ-19 provisional). "
            "Empty means local defaults (127.0.0.1/localhost:3000) when not deployed, "
            "and no CORS when deployed. Wildcard origins are rejected."
        ),
    )

    @field_validator("jwt_secret")
    @classmethod
    def _require_jwt_secret_entropy(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret in _FORBIDDEN_JWT_SECRETS or secret.lower() in _FORBIDDEN_JWT_SECRETS:
            message = "JWT_SECRET must not be a documented placeholder value"
            raise ValueError(message)
        if len(set(secret)) < MIN_JWT_SECRET_UNIQUE_CHARS:
            message = (
                f"JWT_SECRET must contain at least {MIN_JWT_SECRET_UNIQUE_CHARS} "
                "distinct characters"
            )
            raise ValueError(message)
        if jwt_secret_entropy_bits(secret) < MIN_JWT_SECRET_ENTROPY_BITS:
            message = (
                f"JWT_SECRET entropy is too low "
                f"(need at least {MIN_JWT_SECRET_ENTROPY_BITS} bits per character)"
            )
            raise ValueError(message)
        return value

    @field_validator("cors_origins")
    @classmethod
    def _reject_unsafe_cors_origins(cls, value: str) -> str:
        for part in (item.strip() for item in value.split(",")):
            if not part:
                continue
            if part == "*":
                message = "CORS_ORIGINS must not include '*' (credentials cannot be wildcarded)"
                raise ValueError(message)
            parsed = urlparse(part)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                not in {
                    "",
                    "/",
                }
            ):
                message = (
                    "CORS_ORIGINS entries must be absolute http(s) origins "
                    f"without a path (got {part!r})"
                )
                raise ValueError(message)
        return value

    @property
    def docs_enabled(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return not self.is_deployed

    @property
    def cors_origin_list(self) -> list[str]:
        configured = [part.strip() for part in self.cors_origins.split(",") if part.strip()]
        if configured:
            return configured
        if self.is_deployed:
            return []
        return ["http://127.0.0.1:3000", "http://localhost:3000"]
