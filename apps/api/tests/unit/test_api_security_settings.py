"""API settings security validators: JWT entropy and CORS origin hardening."""

import pytest
from pydantic import SecretStr, ValidationError

from doculens.infrastructure.config import Environment
from doculens_api.settings import ApiSettings, jwt_secret_entropy_bits

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://doculens:not-a-secret@127.0.0.1:1/doculens")
STRONG_SECRET = SecretStr("api-test-secret-that-is-at-least-32-bytes-long")  # gitleaks:allow


def test_jwt_secret_entropy_helper_scores_uniform_bytes_high() -> None:
    assert jwt_secret_entropy_bits("a" * 32) < 1.0
    assert jwt_secret_entropy_bits("api-test-secret-that-is-at-least-32-bytes-long") >= 3.0


def test_low_entropy_jwt_secrets_are_rejected() -> None:
    with pytest.raises(ValidationError, match=r"distinct characters|entropy"):
        ApiSettings(
            _env_file=None,
            database_url=DB_URL,
            jwt_secret=SecretStr("a" * 32),
        )


def test_placeholder_jwt_secrets_are_rejected() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        ApiSettings(
            _env_file=None,
            database_url=DB_URL,
            jwt_secret=SecretStr("replace-with-a-random-string-of-at-least-32-characters"),
        )


def test_wildcard_cors_origins_are_rejected() -> None:
    with pytest.raises(ValidationError, match="must not include '\\*'"):
        ApiSettings(
            _env_file=None,
            database_url=DB_URL,
            jwt_secret=STRONG_SECRET,
            cors_origins="*",
        )


def test_cors_origins_must_be_absolute_http_origins() -> None:
    with pytest.raises(ValidationError, match="absolute http"):
        ApiSettings(
            _env_file=None,
            database_url=DB_URL,
            jwt_secret=STRONG_SECRET,
            cors_origins="ftp://evil.example",
        )


def test_cors_origins_must_not_include_a_path() -> None:
    with pytest.raises(ValidationError, match="without a path"):
        ApiSettings(
            _env_file=None,
            database_url=DB_URL,
            jwt_secret=STRONG_SECRET,
            cors_origins="https://app.example.com/dashboard",
        )


def test_explicit_cors_origins_are_accepted() -> None:
    settings = ApiSettings(
        _env_file=None,
        app_env=Environment.LOCAL,
        database_url=DB_URL,
        jwt_secret=STRONG_SECRET,
        cors_origins="https://app.example.com,http://127.0.0.1:3000",
    )
    assert settings.cors_origin_list == [
        "https://app.example.com",
        "http://127.0.0.1:3000",
    ]


def test_daily_ai_quota_settings_default_and_accept_overrides() -> None:
    settings = ApiSettings(
        _env_file=None,
        database_url=DB_URL,
        jwt_secret=STRONG_SECRET,
        ask_daily_rate_limit_attempts=100,
        ai_ops_daily_rate_limit_attempts=50,
        ai_ops_rate_limit_attempts=10,
    )
    assert settings.ask_daily_rate_limit_attempts == 100
    assert settings.ask_daily_rate_limit_window_seconds == 86_400
    assert settings.ai_ops_rate_limit_attempts == 10
    assert settings.ai_ops_daily_rate_limit_attempts == 50
