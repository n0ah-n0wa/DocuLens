import pytest
from fastapi.testclient import TestClient

from doculens_api import __version__

pytestmark = pytest.mark.api


def test_liveness_reports_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "doculens-api", "version": __version__}


def test_openapi_schema_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "DocuLens API"
