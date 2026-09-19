"""Every failure path answers with the §35 envelope and never leaks internals."""

from http import HTTPStatus

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from doculens.domain.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)

pytestmark = pytest.mark.api

probe_router = APIRouter(prefix="/__probe")

DOMAIN_ERRORS: dict[str, type[DomainError]] = {
    "invalid": InvalidInputError,
    "not-found": NotFoundError,
    "conflict": ConflictError,
    "forbidden": PermissionDeniedError,
    "generic": DomainError,
}


@probe_router.get("/domain/{kind}")
async def raise_domain_error(kind: str) -> None:
    raise DOMAIN_ERRORS[kind]()


@probe_router.get("/validate")
async def validate(limit: int) -> dict[str, int]:
    return {"limit": limit}


@probe_router.get("/http")
async def raise_http_error() -> None:
    raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})


@probe_router.get("/crash")
async def crash() -> None:
    message = "database password is hunter2"
    raise RuntimeError(message)


@pytest.fixture
def probe_client(app: FastAPI) -> TestClient:
    app.include_router(probe_router)
    return TestClient(app, raise_server_exceptions=False)


def _envelope(client: TestClient, path: str) -> tuple[int, dict[str, object]]:
    response = client.get(path)
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    return response.status_code, dict(body["error"])


@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        ("invalid", HTTPStatus.BAD_REQUEST, "INVALID_INPUT"),
        ("not-found", HTTPStatus.NOT_FOUND, "NOT_FOUND"),
        ("conflict", HTTPStatus.CONFLICT, "CONFLICT"),
        ("forbidden", HTTPStatus.FORBIDDEN, "PERMISSION_DENIED"),
        ("generic", HTTPStatus.BAD_REQUEST, "DOMAIN_ERROR"),
    ],
)
def test_domain_errors_map_to_status_and_code(
    probe_client: TestClient, kind: str, status: HTTPStatus, code: str
) -> None:
    actual_status, error = _envelope(probe_client, f"/__probe/domain/{kind}")

    assert actual_status == status
    assert error["code"] == code
    assert error["message"] == DOMAIN_ERRORS[kind].default_message
    assert "details" not in error


def test_request_validation_errors_list_each_problem(probe_client: TestClient) -> None:
    status, error = _envelope(probe_client, "/__probe/validate?limit=abc")

    assert status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "Request validation failed."
    details = error["details"]
    assert isinstance(details, list)
    assert details[0]["location"] == "query.limit"
    assert details[0]["type"] == "int_parsing"


def test_unknown_routes_use_the_envelope(probe_client: TestClient) -> None:
    status, error = _envelope(probe_client, "/nowhere")

    assert status == HTTPStatus.NOT_FOUND
    assert error == {"code": "NOT_FOUND", "message": "Not Found", "request_id": error["request_id"]}


def test_http_exceptions_keep_their_status_and_extra_headers(probe_client: TestClient) -> None:
    response = probe_client.get("/__probe/http")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_unexpected_errors_are_generic_and_never_leak_details(probe_client: TestClient) -> None:
    status, error = _envelope(probe_client, "/__probe/crash")

    assert status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"] == "An unexpected error occurred."
    assert "hunter2" not in str(error)
    assert "RuntimeError" not in str(error)
