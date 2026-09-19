from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from doculens.infrastructure.config import Environment, LogFormat, LogLevel
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings


@pytest.fixture
def settings() -> ApiSettings:
    """Explicit settings so tests never depend on the developer's environment or `.env` file."""
    return ApiSettings(
        _env_file=None,
        app_env=Environment.LOCAL,
        log_level=LogLevel.WARNING,
        log_format=LogFormat.JSON,
    )


@pytest.fixture
def app(settings: ApiSettings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A test client bound to a fresh application instance."""
    with TestClient(app) as test_client:
        yield test_client
