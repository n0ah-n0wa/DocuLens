"""API tests against a real, migrated PostgreSQL (skipped without Docker, required in CI)."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from doculens.infrastructure.config import Environment, LogFormat, LogLevel
from doculens.testing.postgres import (
    DatabaseUnavailableError,
    provisioned_database_url,
    upgrade_to_head,
)
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

TEST_JWT_SECRET = "api-integration-secret-that-is-at-least-32-bytes"  # noqa: S105 gitleaks:allow


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    try:
        with provisioned_database_url() as url:
            upgrade_to_head(url)
            yield url
    except DatabaseUnavailableError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(str(exc))


@pytest.fixture
def db_client(migrated_database_url: str, tmp_path: Path) -> Iterator[TestClient]:
    settings = ApiSettings(
        _env_file=None,
        app_env=Environment.LOCAL,
        log_level=LogLevel.WARNING,
        log_format=LogFormat.JSON,
        database_url=SecretStr(migrated_database_url),
        jwt_secret=SecretStr(TEST_JWT_SECRET),
        auth_rate_limit_attempts=1000,
        storage_local_root=tmp_path / "storage",
    )
    with TestClient(create_app(settings)) as client:
        yield client
