"""Settings owned by the API process, on top of the core settings."""

from pydantic import Field, SecretStr

from doculens.infrastructure.config import CoreSettings

MIN_JWT_SECRET_LENGTH = 32


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
        description="Questions allowed per authenticated user per window (§37).",
    )
    ask_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    @property
    def docs_enabled(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return not self.is_deployed
