from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from doculens.infrastructure.config import Environment, LogFormat, LogLevel
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

# Port 1 is never listening, so anything that touches the database fails fast and predictably.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://doculens:not-a-secret@127.0.0.1:1/doculens"


@pytest.fixture
def settings() -> ApiSettings:
    """Explicit settings so tests never depend on the developer's environment or `.env` file."""
    return ApiSettings(
        _env_file=None,
        app_env=Environment.LOCAL,
        log_level=LogLevel.WARNING,
        log_format=LogFormat.JSON,
        database_url=SecretStr(UNREACHABLE_DATABASE_URL),
        health_probe_timeout_seconds=1.0,
    )


@pytest.fixture
def app(settings: ApiSettings) -> FastAPI:
    return create_app(settings, probes=[])


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A test client bound to a fresh application instance with no dependency probes."""
    with TestClient(app) as test_client:
        yield test_client
