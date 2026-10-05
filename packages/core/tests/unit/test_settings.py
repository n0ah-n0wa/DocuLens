import pytest
from pydantic import SecretStr, ValidationError

from doculens.infrastructure.config import (
    ConfigurationError,
    CoreSettings,
    Environment,
    LogFormat,
    LogLevel,
    load_settings,
)

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")


@pytest.fixture(autouse=True)
def _database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL.get_secret_value())


def test_defaults_are_safe_for_local_development() -> None:
    settings = CoreSettings(_env_file=None, database_url=DB_URL)

    assert settings.app_env is Environment.LOCAL
    assert settings.log_level is LogLevel.INFO
    assert settings.log_format is LogFormat.JSON
    assert settings.is_deployed is False


def test_values_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("STORAGE_BUCKET", "doculens-staging-documents")
    monkeypatch.setenv("STORAGE_ENCRYPTION", "AES256")
    monkeypatch.setenv("STORAGE_REGION", "eu-central-1")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-embed-test")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-llm-test")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "10")
    monkeypatch.setenv("CHROMA_URL", "https://chroma.internal:8000")
    monkeypatch.setenv("QUEUE_BACKEND", "sqs")
    monkeypatch.setenv(
        "QUEUE_SQS_URL", "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens"
    )
    monkeypatch.setenv(
        "QUEUE_SQS_DLQ_URL", "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens-dlq"
    )
    monkeypatch.setenv("QUEUE_SQS_REGION", "eu-central-1")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "rediss://redis.internal:6379/0")
    monkeypatch.setenv("CHROMA_API_TOKEN", "chroma-deployed-token")
    monkeypatch.setenv("STORAGE_EXPECTED_BUCKET_OWNER", "123456789012")

    settings = load_settings(CoreSettings, env_file=None)

    assert settings.app_env is Environment.STAGING
    assert settings.log_level is LogLevel.DEBUG
    assert settings.is_deployed is True


def test_local_environment_may_use_console_logs() -> None:
    settings = CoreSettings(
        _env_file=None, database_url=DB_URL, app_env=Environment.LOCAL, log_format=LogFormat.CONSOLE
    )

    assert settings.log_format is LogFormat.CONSOLE


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_deployed_environments_reject_console_logs(environment: Environment) -> None:
    with pytest.raises(ValidationError, match="LOG_FORMAT must be json"):
        CoreSettings(
            _env_file=None, database_url=DB_URL, app_env=environment, log_format=LogFormat.CONSOLE
        )


def test_unknown_environment_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "qa")

    with pytest.raises(ConfigurationError, match="invalid configuration: app_env"):
        load_settings(CoreSettings, env_file=None)


def test_unsafe_deployed_configuration_fails_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_FORMAT", "console")

    with pytest.raises(ConfigurationError, match="unsafe configuration for APP_ENV=production"):
        load_settings(CoreSettings, env_file=None)


def test_deployed_environments_reject_sql_statement_logging() -> None:
    with pytest.raises(ValidationError, match="DATABASE_ECHO must be false"):
        CoreSettings(
            _env_file=None, database_url=DB_URL, app_env=Environment.STAGING, database_echo=True
        )


def test_settings_are_immutable() -> None:
    settings = CoreSettings(_env_file=None, database_url=DB_URL)

    with pytest.raises(ValidationError):
        settings.log_level = LogLevel.DEBUG  # type: ignore[misc]  # frozen model is the point


def test_unknown_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_UNRELATED_VARIABLE", "value")

    assert load_settings(CoreSettings, env_file=None).app_env is Environment.LOCAL


DEPLOYED_BASE = {
    "storage_backend": "s3",
    "storage_bucket": "doculens-prod-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": "AES256",
    "embedding_provider": "openai",
    "embedding_api_key": SecretStr("sk-embed"),
    "llm_provider": "openai",
    "llm_api_key": SecretStr("sk-llm"),
    "chroma_url": "https://chroma.internal:8000",
    "queue_backend": "sqs",
    "queue_sqs_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens",
    "queue_sqs_dlq_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens-dlq",
    "queue_sqs_region": "eu-central-1",
    "rate_limit_backend": "redis",
    "redis_url": "rediss://redis.internal:6379/0",
    "storage_expected_bucket_owner": "123456789012",
    "chroma_api_token": SecretStr("chroma-deployed-token"),
    "max_file_size_mb": 10,
    "llm_timeout_seconds": 20.0,
}


def test_deployed_environments_require_shared_redis_rate_limits() -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        app_env=Environment.PRODUCTION,
        **DEPLOYED_BASE,  # type: ignore[arg-type]
    )
    assert settings.rate_limit_backend.value == "redis"
    assert settings.redis_url is not None
    assert settings.redis_url.startswith("rediss://")


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"rate_limit_backend": "memory"}, "RATE_LIMIT_BACKEND must be redis"),
        ({"rate_limit_backend": "auto"}, "RATE_LIMIT_BACKEND must be redis"),
        ({"redis_url": None}, "REDIS_URL is required"),
        ({"redis_url": "redis://redis.internal:6379/0"}, "REDIS_URL must use rediss://"),
    ],
)
def test_deployed_environments_reject_in_process_rate_limits(
    overrides: dict[str, object], fragment: str
) -> None:
    with pytest.raises(ValidationError, match=fragment):
        CoreSettings(
            _env_file=None,
            database_url=DB_URL,
            app_env=Environment.STAGING,
            **{**DEPLOYED_BASE, **overrides},  # type: ignore[arg-type]
        )
