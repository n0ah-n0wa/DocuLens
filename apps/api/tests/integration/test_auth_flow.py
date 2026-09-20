"""The full authentication flow end to end against PostgreSQL."""

from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value


def test_register_login_refresh_logout_against_the_database(db_client: TestClient) -> None:
    email = f"flow-{uuid4().hex}@example.com"

    assert db_client.get("/health/ready").status_code == HTTPStatus.OK

    registered = db_client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert registered.status_code == HTTPStatus.CREATED, registered.text

    duplicate = db_client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert duplicate.status_code == HTTPStatus.CONFLICT

    login = db_client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == HTTPStatus.OK, login.text
    tokens = login.json()

    me = db_client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == HTTPStatus.OK
    assert me.json()["email"] == email

    rotated = db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert rotated.status_code == HTTPStatus.OK
    reused = db_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reused.status_code == HTTPStatus.UNAUTHORIZED

    fresh = db_client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()
    logout = db_client.post("/api/v1/auth/logout", json={"refresh_token": fresh["refresh_token"]})
    assert logout.status_code == HTTPStatus.NO_CONTENT
    after_logout = db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]}
    )
    assert after_logout.status_code == HTTPStatus.UNAUTHORIZED
