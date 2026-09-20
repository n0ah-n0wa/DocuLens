from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from doculens.infrastructure.config import Environment, LogFormat, LogLevel, VectorStoreKind
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

# Port 1 is never listening, so anything that touches the database fails fast and predictably.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://doculens:not-a-secret@127.0.0.1:1/doculens"
TEST_JWT_SECRET = "api-test-secret-that-is-at-least-32-bytes-long"  # noqa: S105 gitleaks:allow


@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    """Explicit settings so tests never depend on the developer's environment or `.env` file."""
    return ApiSettings(
        _env_file=None,
        app_env=Environment.LOCAL,
        log_level=LogLevel.WARNING,
        log_format=LogFormat.JSON,
        database_url=SecretStr(UNREACHABLE_DATABASE_URL),
        health_probe_timeout_seconds=1.0,
        jwt_secret=SecretStr(TEST_JWT_SECRET),
        auth_rate_limit_attempts=1000,
        storage_local_root=tmp_path / "storage",
        vector_store=VectorStoreKind.MEMORY,
    )


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def app(settings: ApiSettings, store: InMemoryStore) -> FastAPI:
    """An application with no dependency probes and an in-memory unit of work."""
    return create_app(settings, probes=[], unit_of_work_factory=lambda: InMemoryUnitOfWork(store))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
