"""Rate limits for document upload, processing and question answering (SPECIFICATIONS.md §37)."""

from dataclasses import replace
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from doculens.domain.documents import ProcessingStatus
from doculens.domain.storage import PDF_MIME_TYPE
from doculens.infrastructure.config import Environment, LogFormat, LogLevel, VectorStoreKind
from doculens.infrastructure.ratelimit import InMemoryRateLimiter
from doculens.testing.factories import Factories
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


def _settings(**overrides: object) -> ApiSettings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": Environment.LOCAL,
        "log_level": LogLevel.WARNING,
        "log_format": LogFormat.JSON,
        "database_url": SecretStr(UNREACHABLE_DATABASE_URL),
        "jwt_secret": SecretStr(JWT_SECRET),
        "auth_rate_limit_attempts": 1000,
        "vector_store": VectorStoreKind.MEMORY,
    }
    base.update(overrides)
    return ApiSettings(**base)  # type: ignore[arg-type]


def _app(settings: ApiSettings, clock: Clock, tmp_path: Path) -> FastAPI:
    store = InMemoryStore()
    app = create_app(
        settings.model_copy(update={"storage_local_root": tmp_path / "storage"}),
        probes=[],
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        rate_limiter=InMemoryRateLimiter(clock=clock),
    )
    app.state.test_store = store
    return app


@pytest.fixture
def limited_app(clock: Clock, tmp_path: Path) -> FastAPI:
    return _app(
        _settings(
            upload_rate_limit_attempts=ATTEMPTS,
            upload_rate_limit_window_seconds=WINDOW,
            ask_rate_limit_attempts=ATTEMPTS,
            ask_rate_limit_window_seconds=WINDOW,
            ai_ops_rate_limit_attempts=1000,
            ai_ops_daily_rate_limit_attempts=1000,
        ),
        clock,
        tmp_path,
    )


def _client(app: FastAPI, address: str = "127.0.0.1") -> TestClient:
    return TestClient(app, client=(address, 12345))


def _auth_headers(client: TestClient, email: str = "owner@example.com") -> dict[str, str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == HTTPStatus.OK, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_document_uploads_are_throttled_per_user(limited_app: FastAPI) -> None:
    with _client(limited_app) as client:
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
    with _client(limited_app) as client:
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


@pytest.fixture
def daily_limited_app(clock: Clock, tmp_path: Path) -> FastAPI:
    return _app(
        _settings(
            upload_rate_limit_attempts=1000,
            ask_rate_limit_attempts=1000,
            ask_rate_limit_window_seconds=WINDOW,
            ask_daily_rate_limit_attempts=ATTEMPTS,
            ask_daily_rate_limit_window_seconds=WINDOW,
            ai_ops_rate_limit_attempts=1000,
            ai_ops_daily_rate_limit_attempts=ATTEMPTS,
            ai_ops_daily_rate_limit_window_seconds=WINDOW,
        ),
        clock,
        tmp_path,
    )


def test_questions_are_throttled_by_daily_quota(daily_limited_app: FastAPI) -> None:
    with _client(daily_limited_app) as client:
        headers = _auth_headers(client, email="daily@example.com")
        conversation_id = client.post(
            "/api/v1/conversations", json={"title": "Daily"}, headers=headers
        ).json()["id"]
        url = f"/api/v1/conversations/{conversation_id}/messages"
        statuses = [
            client.post(url, json={"question": f"Day {index}?"}, headers=headers).status_code
            for index in range(ATTEMPTS + 1)
        ]

    assert HTTPStatus.TOO_MANY_REQUESTS not in statuses[:ATTEMPTS]
    assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS


def test_sync_and_stream_questions_share_one_budget(limited_app: FastAPI) -> None:
    with _client(limited_app) as client:
        headers = _auth_headers(client, email="stream@example.com")
        conversation_id = client.post(
            "/api/v1/conversations", json={"title": "Shared"}, headers=headers
        ).json()["id"]
        base = f"/api/v1/conversations/{conversation_id}/messages"
        for index in range(ATTEMPTS - 1):
            assert (
                client.post(base, json={"question": f"sync {index}?"}, headers=headers).status_code
                != HTTPStatus.TOO_MANY_REQUESTS
            )
        assert (
            client.post(f"{base}/stream", json={"question": "stream?"}, headers=headers).status_code
            != HTTPStatus.TOO_MANY_REQUESTS
        )
        blocked = client.post(base, json={"question": "over?"}, headers=headers)
    assert blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


def test_forwarded_for_cannot_bypass_per_ip_auth_budget(clock: Clock, tmp_path: Path) -> None:
    app = _app(
        _settings(
            auth_rate_limit_attempts=ATTEMPTS,
            auth_rate_limit_window_seconds=WINDOW,
        ),
        clock,
        tmp_path,
    )
    with _client(app, "10.9.8.7") as client:
        for index in range(ATTEMPTS):
            assert (
                client.post(
                    "/api/v1/auth/login",
                    json={"email": f"u{index}@example.com", "password": PASSWORD},
                    headers={"X-Forwarded-For": f"203.0.113.{index}"},
                ).status_code
                == HTTPStatus.UNAUTHORIZED
            )
        spoofed = client.post(
            "/api/v1/auth/login",
            json={"email": "last@example.com", "password": PASSWORD},
            headers={"X-Forwarded-For": "198.51.100.1"},
        )
    assert spoofed.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert spoofed.headers["Retry-After"] == str(WINDOW)


def test_ask_budget_is_shared_across_users_on_one_ip(clock: Clock, tmp_path: Path) -> None:
    app = _app(
        _settings(
            ask_rate_limit_attempts=ATTEMPTS,
            ask_rate_limit_window_seconds=WINDOW,
            ask_daily_rate_limit_attempts=1000,
            ai_ops_rate_limit_attempts=1000,
            ai_ops_daily_rate_limit_attempts=1000,
        ),
        clock,
        tmp_path,
    )
    with _client(app, "10.4.5.6") as client:
        for index in range(ATTEMPTS):
            headers = _auth_headers(client, email=f"peer{index}@example.com")
            conversation_id = client.post(
                "/api/v1/conversations", json={"title": f"C{index}"}, headers=headers
            ).json()["id"]
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"question": f"Q{index}?"},
                headers=headers,
            )
            assert response.status_code != HTTPStatus.TOO_MANY_REQUESTS, response.text
        headers = _auth_headers(client, email="overflow@example.com")
        conversation_id = client.post(
            "/api/v1/conversations", json={"title": "Over"}, headers=headers
        ).json()["id"]
        blocked = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"question": "blocked?"},
            headers=headers,
        )
    assert blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_reprocess_and_reindex_share_the_processing_budget(clock: Clock, tmp_path: Path) -> None:
    app = _app(
        _settings(
            upload_rate_limit_attempts=1000,
            ask_rate_limit_attempts=1000,
            ai_ops_rate_limit_attempts=ATTEMPTS,
            ai_ops_rate_limit_window_seconds=WINDOW,
            ai_ops_daily_rate_limit_attempts=1000,
        ),
        clock,
        tmp_path,
    )
    store: InMemoryStore = app.state.test_store
    with _client(app) as client:
        headers = _auth_headers(client, email="ops@example.com")
        me = client.get("/api/v1/users/me", headers=headers)
        assert me.status_code == HTTPStatus.OK
        document = replace(
            Factories.document(UUID(me.json()["id"])),
            processing_status=ProcessingStatus.READY,
        )
        store.documents[document.id] = document

        statuses: list[int] = []
        for _ in range(ATTEMPTS):
            store.documents[document.id] = replace(
                store.documents[document.id], processing_status=ProcessingStatus.READY
            )
            statuses.append(
                client.post(
                    f"/api/v1/documents/{document.id}/reprocess", headers=headers
                ).status_code
            )
        store.documents[document.id] = replace(
            store.documents[document.id], processing_status=ProcessingStatus.READY
        )
        blocked = client.post(f"/api/v1/documents/{document.id}/reindex", headers=headers)

    assert statuses == [HTTPStatus.OK] * ATTEMPTS
    assert blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert blocked.headers["Retry-After"] == str(WINDOW)


def test_upload_consumes_the_shared_processing_budget(clock: Clock, tmp_path: Path) -> None:
    app = _app(
        _settings(
            upload_rate_limit_attempts=1000,
            upload_rate_limit_window_seconds=WINDOW,
            ai_ops_rate_limit_attempts=ATTEMPTS,
            ai_ops_rate_limit_window_seconds=WINDOW,
            ai_ops_daily_rate_limit_attempts=1000,
        ),
        clock,
        tmp_path,
    )
    with _client(app) as client:
        headers = _auth_headers(client, email="proc@example.com")
        for index in range(ATTEMPTS):
            response = client.post(
                "/api/v1/documents",
                headers=headers,
                files={
                    "file": (
                        f"p{index}.pdf",
                        pdf_with_pages([f"page {index}"]),
                        PDF_MIME_TYPE,
                    )
                },
            )
            assert response.status_code == HTTPStatus.CREATED, response.text
        blocked = client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("over.pdf", pdf_with_pages(["over"]), PDF_MIME_TYPE)},
        )
    assert blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS
