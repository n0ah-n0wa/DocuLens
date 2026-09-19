from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api


def test_openapi_document_describes_the_error_envelope(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == HTTPStatus.OK
    document = response.json()
    assert document["info"]["title"] == "DocuLens API"
    assert "ErrorResponse" in document["components"]["schemas"]
    liveness = document["paths"]["/health/live"]["get"]
    assert liveness["tags"] == ["health"]
    assert set(liveness["responses"]) >= {"200", "422", "500"}
    readiness = document["paths"]["/health/ready"]["get"]
    assert "503" in readiness["responses"]


def test_interactive_docs_are_served_when_enabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == HTTPStatus.OK


def test_docs_can_be_disabled_by_configuration(settings: ApiSettings) -> None:
    disabled = settings.model_copy(update={"api_docs_enabled": False})

    with TestClient(create_app(disabled)) as client:
        assert client.get("/openapi.json").status_code == HTTPStatus.NOT_FOUND
        assert client.get("/docs").status_code == HTTPStatus.NOT_FOUND
        assert client.get("/health/live").status_code == HTTPStatus.OK
