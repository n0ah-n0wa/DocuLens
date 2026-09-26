from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api

# Every public route from SPECIFICATIONS.md §33 plus platform probes. Undocumented paths fail.
SPEC_PATHS: dict[str, set[str]] = {
    "/health/live": {"get"},
    "/health/ready": {"get"},
    "/api/v1/auth/register": {"post"},
    "/api/v1/auth/login": {"post"},
    "/api/v1/auth/refresh": {"post"},
    "/api/v1/auth/logout": {"post"},
    "/api/v1/users/me": {"get"},
    "/api/v1/documents": {"get", "post"},
    "/api/v1/documents/{document_id}": {"get", "patch", "delete"},
    "/api/v1/documents/{document_id}/reprocess": {"post"},
    "/api/v1/documents/{document_id}/reindex": {"post"},
    "/api/v1/collections": {"get", "post"},
    "/api/v1/collections/{collection_id}": {"get", "patch", "delete"},
    "/api/v1/conversations": {"get", "post"},
    "/api/v1/conversations/{conversation_id}": {"get", "patch", "delete"},
    "/api/v1/conversations/{conversation_id}/messages": {"get", "post"},
}


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


def test_every_public_route_is_documented_and_no_extras_exist(client: TestClient) -> None:
    """§33 inventory plus health: every registered path appears in OpenAPI with ErrorResponse."""
    document = client.get("/openapi.json").json()
    paths = document["paths"]

    assert set(paths) == set(SPEC_PATHS)
    for path, methods in SPEC_PATHS.items():
        assert set(paths[path]) >= methods, path
        for method in methods:
            operation = paths[path][method]
            responses = operation["responses"]
            assert "422" in responses, f"{method.upper()} {path}"
            assert "500" in responses, f"{method.upper()} {path}"
            for status, body in responses.items():
                if status in {"200", "201", "204"}:
                    continue
                content = body.get("content", {}).get("application/json", {})
                schema = content.get("schema", {})
                ref = schema.get("$ref", "")
                assert ref, f"{method.upper()} {path} status {status} missing JSON schema"
                assert "ErrorResponse" in ref or "ReadinessResponse" in ref, (
                    f"{method.upper()} {path} status {status}"
                )

    upload = paths["/api/v1/documents"]["post"]
    assert "401" in upload["responses"]
    assert "409" in upload["responses"]
    assert "413" in upload["responses"]
    assert "429" in upload["responses"]
    assert "multipart/form-data" in upload["requestBody"]["content"]

    ask = paths["/api/v1/conversations/{conversation_id}/messages"]["post"]
    assert "429" in ask["responses"]
    question_schema = ask["requestBody"]["content"]["application/json"]["schema"]
    # Resolve local $ref if FastAPI inlines AskRequest.
    props = (
        question_schema.get("properties")
        or document["components"]["schemas"][question_schema["$ref"].rsplit("/", 1)[-1]][
            "properties"
        ]
    )
    assert props["question"]["maxLength"] == 100_000


def test_interactive_docs_are_served_when_enabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == HTTPStatus.OK


def test_docs_can_be_disabled_by_configuration(settings: ApiSettings) -> None:
    disabled = settings.model_copy(update={"api_docs_enabled": False})

    with TestClient(create_app(disabled)) as client:
        assert client.get("/openapi.json").status_code == HTTPStatus.NOT_FOUND
        assert client.get("/docs").status_code == HTTPStatus.NOT_FOUND
        assert client.get("/health/live").status_code == HTTPStatus.OK
