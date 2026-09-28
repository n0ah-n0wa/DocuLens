"""CORS hardening: credentials require an explicit allow-list (no wildcards)."""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api

ALLOWED_ORIGIN = "https://app.example.com"


@pytest.fixture
def cors_client(settings: ApiSettings) -> TestClient:
    app = create_app(
        settings.model_copy(update={"cors_origins": ALLOWED_ORIGIN}),
        probes=[],
    )
    return TestClient(app)


def test_allowed_origin_receives_cors_headers(cors_client: TestClient) -> None:
    response = cors_client.options(
        "/health/live",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    allow_methods = response.headers["access-control-allow-methods"]
    assert "GET" in allow_methods
    assert "*" not in allow_methods


def test_disallowed_origin_does_not_receive_cors_headers(cors_client: TestClient) -> None:
    response = cors_client.get("/health/live", headers={"Origin": "https://evil.example"})
    assert response.status_code == HTTPStatus.OK
    assert "access-control-allow-origin" not in response.headers
