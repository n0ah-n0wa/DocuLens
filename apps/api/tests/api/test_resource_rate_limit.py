"""Rate limits for document upload and question answering (SPECIFICATIONS.md §37)."""

from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from doculens.domain.storage import PDF_MIME_TYPE
from doculens.infrastructure.config import Environment, LogFormat, LogLevel, VectorStoreKind
from doculens.infrastructure.ratelimit import InMemoryRateLimiter
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.pdfs import pdf_with_pages
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api

ATTEMPTS = 3
WINDOW = 60
PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value
JWT_SECRET = "rate-limit-test-secret-that-is-at-least-32-bytes"  # noqa: S105 - test-only value
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://doculens:not-a-secret@127.0.0.1:1/doculens"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def limited_app(clock: Clock, tmp_path: Path) -> FastAPI:
    settings = ApiSettings(
        _env_file=None,
        app_env=Environment.LOCAL,
        log_level=LogLevel.WARNING,
        log_format=LogFormat.JSON,
        database_url=SecretStr(UNREACHABLE_DATABASE_URL),
        jwt_secret=SecretStr(JWT_SECRET),
        auth_rate_limit_attempts=1000,
        upload_rate_limit_attempts=ATTEMPTS,
        upload_rate_limit_window_seconds=WINDOW,
        ask_rate_limit_attempts=ATTEMPTS,
        ask_rate_limit_window_seconds=WINDOW,
        storage_local_root=tmp_path / "storage",
        vector_store=VectorStoreKind.MEMORY,
    )
    store = InMemoryStore()
    return create_app(
        settings,
        probes=[],
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        rate_limiter=InMemoryRateLimiter(clock=clock),
    )


def _auth_headers(client: TestClient, email: str = "owner@example.com") -> dict[str, str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == HTTPStatus.OK, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_document_uploads_are_throttled_per_user(limited_app: FastAPI) -> None:
    with TestClient(limited_app) as client:
        headers = _auth_headers(client)
        statuses: list[int] = []
        for index in range(ATTEMPTS + 1):
            payload = pdf_with_pages([f"upload page {index}"])
            response = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": (f"doc{index}.pdf", payload, PDF_MIME_TYPE)},
            )
            statuses.append(response.status_code)

    assert statuses[:ATTEMPTS] == [HTTPStatus.CREATED] * ATTEMPTS
    assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS


def test_questions_are_throttled_per_user(limited_app: FastAPI) -> None:
    with TestClient(limited_app) as client:
        headers = _auth_headers(client)
        conversation_id = client.post(
            "/api/v1/conversations", json={"title": "Quota"}, headers=headers
        ).json()["id"]
        url = f"/api/v1/conversations/{conversation_id}/messages"
        statuses = [
            client.post(url, json={"question": f"What is {index}?"}, headers=headers).status_code
            for index in range(ATTEMPTS + 1)
        ]
        blocked = client.post(url, json={"question": "again?"}, headers=headers)

    assert HTTPStatus.TOO_MANY_REQUESTS not in statuses[:ATTEMPTS]
    assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert blocked.headers["Retry-After"] == str(WINDOW)
