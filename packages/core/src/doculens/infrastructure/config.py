"""Settings shared by every DocuLens process (SPECIFICATIONS.md §70, §87).

Composition roots (the API and the worker) load settings once at start-up through
:func:`load_settings`; a misconfigured deployed environment fails the process with a clear message
instead of running with unsafe defaults. Values come from environment variables and, for local
development only, a ``.env`` file. Nothing in this module holds or defaults a secret.

Interface packages extend :class:`CoreSettings` with the fields they own.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_URL_SCHEME = "postgresql+asyncpg"
BUCKET_NAME_PATTERN = r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
AWS_ACCOUNT_ID_PATTERN = r"^[0-9]{12}$"
DEFAULT_MAX_OBJECT_BYTES = 100 * 1024 * 1024
ENDPOINT_SCHEMES = ("http://", "https://")


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


class StorageBackend(StrEnum):
    S3 = "s3"
    FILESYSTEM = "filesystem"


class StorageEncryption(StrEnum):
    """Server-side encryption requested for every stored object (§53, §57)."""

    NONE = "none"
    AES256 = "AES256"
    AWS_KMS = "aws:kms"


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

    database_url: SecretStr = Field(
        description="SQLAlchemy URL for PostgreSQL, e.g. postgresql+asyncpg://user:pass@host/db."
    )
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=5, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    database_pool_recycle_seconds: int = Field(
        default=1800, ge=60, description="Recycle idle connections before proxies drop them."
    )
    database_echo: bool = Field(default=False, description="Log every SQL statement (local only).")

    storage_backend: StorageBackend = Field(
        default=StorageBackend.FILESYSTEM,
        description="Where document originals live (§11): s3 (AWS or S3-compatible) or filesystem.",
    )
    storage_local_root: Path = Field(
        default=Path(".local/storage"),
        description="Root directory of the filesystem backend (local development and tests).",
    )
    storage_bucket: str | None = Field(default=None, pattern=BUCKET_NAME_PATTERN)
    storage_region: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,32}$")
    storage_endpoint_url: str | None = Field(
        default=None, description="Custom S3 endpoint (MinIO locally); unset for AWS S3."
    )
    storage_access_key_id: str | None = Field(
        default=None, description="Static credentials are for local S3-compatible stores only."
    )
    storage_secret_access_key: SecretStr | None = None
    storage_force_path_style: bool = Field(
        default=False, description="Path-style addressing, required by MinIO."
    )
    storage_encryption: StorageEncryption = Field(
        default=StorageEncryption.NONE,
        description="Required to be AES256 or aws:kms when deployed.",
    )
    storage_kms_key_id: str | None = Field(default=None, max_length=2048)
    storage_expected_bucket_owner: str | None = Field(
        default=None,
        pattern=AWS_ACCOUNT_ID_PATTERN,
        description="AWS account that must own the bucket; sent with every request when set.",
    )
    storage_max_object_bytes: int = Field(
        default=DEFAULT_MAX_OBJECT_BYTES,
        ge=1024,
        description="Largest object the adapters accept on upload or read back on download.",
    )
    max_file_size_mb: int = Field(
        default=50, ge=1, le=5120, description="Largest accepted upload (§10.2)."
    )
    max_pages_per_document: int = Field(default=500, ge=1, le=100_000)
    max_documents_per_user: int = Field(default=100, ge=1, le=1_000_000)
    pdf_extraction_timeout_seconds: float = Field(
        default=120.0, gt=0, le=3600, description="Wall-clock bound for one parse (§53, §64)."
    )
    pdf_extraction_memory_limit_mb: int = Field(
        default=1024,
        ge=0,
        le=65536,
        description="Address-space cap of the parser process on POSIX hosts; 0 disables it.",
    )
    pdf_max_characters_per_page: int = Field(default=1_000_000, ge=1000, le=50_000_000)
    pdf_max_total_characters: int = Field(
        default=20_000_000,
        ge=1000,
        le=500_000_000,
        description="Text budget for one document; bounds worker memory whatever the file holds.",
    )
    chunk_size: int = Field(
        default=512, ge=16, le=8192, description="Chunk budget in tokens (§14)."
    )
    chunk_overlap: int = Field(default=64, ge=0, le=4096)
    min_chunk_size: int = Field(default=64, ge=1, le=8192)

    storage_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    storage_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    storage_max_attempts: int = Field(default=3, ge=1, le=10)
    storage_max_concurrent_transfers: int = Field(default=8, ge=1, le=64)

    @field_validator("database_url")
    @classmethod
    def _require_async_postgres_url(cls, value: SecretStr) -> SecretStr:
        scheme = value.get_secret_value().split("://", 1)[0]
        if scheme != DATABASE_URL_SCHEME:
            message = f"DATABASE_URL must start with {DATABASE_URL_SCHEME}://"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _validate_storage(self) -> Self:
        problems: list[str] = []
        if self.storage_backend is StorageBackend.S3 and self.storage_bucket is None:
            problems.append("STORAGE_BUCKET is required when STORAGE_BACKEND=s3")
        if self.storage_endpoint_url is not None:
            if not self.storage_endpoint_url.startswith(ENDPOINT_SCHEMES):
                problems.append("STORAGE_ENDPOINT_URL must start with http:// or https://")
            authority = self.storage_endpoint_url.split("://", 1)[-1].split("/", 1)[0]
            if "@" in authority:
                problems.append("STORAGE_ENDPOINT_URL must not embed credentials")
        if (self.storage_access_key_id is None) != (self.storage_secret_access_key is None):
            problems.append(
                "STORAGE_ACCESS_KEY_ID and STORAGE_SECRET_ACCESS_KEY must be set together"
            )
        if self.storage_encryption is StorageEncryption.AWS_KMS and not self.storage_kms_key_id:
            problems.append("STORAGE_KMS_KEY_ID is required when STORAGE_ENCRYPTION=aws:kms")
        if self.storage_kms_key_id and self.storage_encryption is not StorageEncryption.AWS_KMS:
            problems.append("STORAGE_KMS_KEY_ID needs STORAGE_ENCRYPTION=aws:kms")
        if self.max_file_size_mb * 1024 * 1024 > self.storage_max_object_bytes:
            problems.append("MAX_FILE_SIZE_MB must not exceed STORAGE_MAX_OBJECT_BYTES")
        if self.pdf_max_characters_per_page > self.pdf_max_total_characters:
            problems.append("PDF_MAX_CHARACTERS_PER_PAGE must not exceed PDF_MAX_TOTAL_CHARACTERS")
        if self.chunk_overlap >= self.chunk_size:
            problems.append("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.min_chunk_size > self.chunk_size:
            problems.append("MIN_CHUNK_SIZE must not exceed CHUNK_SIZE")
        if problems:
            message = "invalid storage configuration: " + "; ".join(problems)
            raise ValueError(message)
        return self

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
        if self.database_echo:
            problems.append("DATABASE_ECHO must be false (statement logging can expose data, §68)")
        if self.storage_backend is not StorageBackend.S3:
            problems.append("STORAGE_BACKEND must be s3 (originals must live in S3, §11)")
        if self.storage_encryption is StorageEncryption.NONE:
            problems.append(
                "STORAGE_ENCRYPTION must be AES256 or aws:kms (encryption at rest, §53)"
            )
        if self.storage_region is None:
            problems.append("STORAGE_REGION is required (request signing must not guess a region)")
        if self.storage_endpoint_url is not None and not self.storage_endpoint_url.startswith(
            "https://"
        ):
            problems.append("STORAGE_ENDPOINT_URL must use https (encryption in transit, §53)")
        if self.storage_access_key_id is not None:
            problems.append(
                "STORAGE_ACCESS_KEY_ID must be unset (use the execution role, least privilege §57)"
            )
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
