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
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("CHROMA_URL", "https://chroma.internal:8000")
    monkeypatch.setenv("QUEUE_BACKEND", "sqs")
    monkeypatch.setenv(
        "QUEUE_SQS_URL", "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens"
    )
    monkeypatch.setenv(
        "QUEUE_SQS_DLQ_URL", "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens-dlq"
    )
    monkeypatch.setenv("QUEUE_SQS_REGION", "eu-central-1")

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
