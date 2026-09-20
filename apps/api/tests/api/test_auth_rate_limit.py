"""Brute-force resistance of the authentication endpoints (§37).

Every auth endpoint has a per-address budget; login additionally has a per-account budget so a
distributed attacker cannot spread guesses for one account across many addresses.
"""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from doculens.infrastructure.config import Environment, LogFormat, LogLevel
from doculens.infrastructure.ratelimit import InMemoryRateLimiter
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
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
def limited_app(clock: Clock) -> FastAPI:
    settings = ApiSettings(
        _env_file=None,
        app_env=Environment.LOCAL,
        log_level=LogLevel.WARNING,
        log_format=LogFormat.JSON,
        database_url=SecretStr(UNREACHABLE_DATABASE_URL),
        jwt_secret=SecretStr(JWT_SECRET),
        auth_rate_limit_attempts=ATTEMPTS,
        auth_rate_limit_window_seconds=WINDOW,
    )
    store = InMemoryStore()
    return create_app(
        settings,
        probes=[],
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        rate_limiter=InMemoryRateLimiter(clock=clock),
    )


def _client(app: FastAPI, address: str) -> TestClient:
    return TestClient(app, client=(address, 12345))


def _login(client: TestClient, email: str) -> int:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return response.status_code


def test_login_attempts_from_one_address_are_capped(limited_app: FastAPI) -> None:
    with _client(limited_app, "10.0.0.1") as client:
        for index in range(ATTEMPTS):
            assert _login(client, f"user{index}@example.com") == HTTPStatus.UNAUTHORIZED
        response = client.post(
            "/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD}
        )

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.headers["Retry-After"] == str(WINDOW)
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert "WWW-Authenticate" not in response.headers


def test_attempts_against_one_account_are_capped_across_addresses(limited_app: FastAPI) -> None:
    target = "victim@example.com"
    for index in range(ATTEMPTS):
        with _client(limited_app, f"10.0.1.{index}") as client:
            assert _login(client, f" {target.upper()} ") == HTTPStatus.UNAUTHORIZED

    with _client(limited_app, "10.0.2.99") as client:
        assert _login(client, target) == HTTPStatus.TOO_MANY_REQUESTS
        # Other accounts are unaffected from that address.
        assert _login(client, "someone-else@example.com") == HTTPStatus.UNAUTHORIZED


def test_the_budget_returns_after_the_window(limited_app: FastAPI, clock: Clock) -> None:
    with _client(limited_app, "10.0.0.2") as client:
        for _ in range(ATTEMPTS):
            _login(client, "user@example.com")
        assert _login(client, "user@example.com") == HTTPStatus.TOO_MANY_REQUESTS
        clock.now += timedelta(seconds=WINDOW)
        assert _login(client, "user@example.com") == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/auth/register", {"email": "new@example.com", "password": PASSWORD}),
        ("/api/v1/auth/refresh", {"refresh_token": "not-a-token"}),
        ("/api/v1/auth/logout", {"refresh_token": "not-a-token"}),
    ],
)
def test_every_auth_endpoint_is_throttled_per_address(
    limited_app: FastAPI, path: str, body: dict[str, str]
) -> None:
    with _client(limited_app, "10.0.0.3") as client:
        statuses = [client.post(path, json=body).status_code for _ in range(ATTEMPTS + 1)]

    assert HTTPStatus.TOO_MANY_REQUESTS not in statuses[:ATTEMPTS]
    assert statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS


def test_throttling_happens_before_any_credential_work(limited_app: FastAPI) -> None:
    """A throttled registration must not create the account."""
    with _client(limited_app, "10.0.0.4") as client:
        for _ in range(ATTEMPTS):
            client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "x"})
        blocked = client.post(
            "/api/v1/auth/register", json={"email": "late@example.com", "password": PASSWORD}
        )
        assert blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS

    with _client(limited_app, "10.0.0.5") as client:
        assert _login(client, "late@example.com") == HTTPStatus.UNAUTHORIZED
