"""Settings shared by every DocuLens process (SPECIFICATIONS.md §70, §87).

Composition roots (the API and the worker) load settings once at start-up through
:func:`load_settings`; a misconfigured deployed environment fails the process with a clear message
instead of running with unsafe defaults. Values come from environment variables and, for local
development only, a ``.env`` file. Nothing in this module holds or defaults a secret.

Interface packages extend :class:`CoreSettings` with the fields they own.
"""

from enum import StrEnum
from typing import Self

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ConfigurationError(RuntimeError):
    """Raised at start-up when the environment does not describe a valid configuration."""


class CoreSettings(BaseSettings):
    """Configuration every process needs. Field names map to environment variables verbatim."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_default=True,
    )

    app_env: Environment = Field(
        default=Environment.LOCAL,
        description="Deployment environment (local, staging, production).",
    )
    log_level: LogLevel = Field(default=LogLevel.INFO, description="Minimum level that is emitted.")
    log_format: LogFormat = Field(
        default=LogFormat.JSON,
        description="json for machine-readable logs; console is for local development only.",
    )

    @property
    def is_deployed(self) -> bool:
        """True for staging and production, where local-development conveniences are forbidden."""
        return self.app_env is not Environment.LOCAL

    @model_validator(mode="after")
    def _reject_unsafe_deployed_configuration(self) -> Self:
        if not self.is_deployed:
            return self
        problems: list[str] = []
        if self.log_format is not LogFormat.JSON:
            problems.append("LOG_FORMAT must be json (structured logging is required, §50)")
        if problems:
            message = f"unsafe configuration for APP_ENV={self.app_env}: " + "; ".join(problems)
            raise ValueError(message)
        return self


def load_settings[SettingsT: CoreSettings](
    settings_type: type[SettingsT], *, env_file: str | None = ".env"
) -> SettingsT:
    """Build and validate settings from the environment, failing loudly on invalid values."""
    try:
        return settings_type(_env_file=env_file)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'settings'}: {error['msg']}"
            for error in exc.errors()
        )
        message = f"invalid configuration: {problems}"
        raise ConfigurationError(message) from exc
