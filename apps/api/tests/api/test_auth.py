"""HTTP contract of authentication (§8, §9, §35) with the in-memory unit of work."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from doculens.application.auth import TokenClaims, TokenType
from doculens.domain.users import UserStatus
from doculens.infrastructure.security.tokens import JwtTokenCodec
from doculens.testing.fakes import InMemoryStore
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api

EMAIL = "alice@example.com"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value


def _register(
    client: TestClient, email: str = EMAIL, password: str = PASSWORD
) -> dict[str, object]:
    response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == HTTPStatus.CREATED, response.text
    return dict(response.json())


def _login(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> dict[str, object]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == HTTPStatus.OK, response.text
    return dict(response.json())


def _bearer(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_the_public_profile_only(client: TestClient) -> None:
    body = _register(client, email="Alice@Example.com")

    assert body["email"] == EMAIL
    assert body["status"] == "ACTIVE"
    assert set(body) == {"id", "email", "status", "created_at", "last_login_at"}
    assert PASSWORD not in str(body)


def test_duplicate_registration_is_a_conflict(client: TestClient) -> None:
    _register(client)
    response = client.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"email": "not-an-email", "password": PASSWORD}, "INVALID_EMAIL"),
        ({"email": EMAIL, "password": "short"}, "PASSWORD_POLICY_VIOLATION"),
    ],
)
def test_registration_input_is_validated_without_echoing_the_password(
    client: TestClient, payload: dict[str, str], code: str
) -> None:
    response = client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["error"]["code"] == code
    assert payload["password"] not in response.text


def test_missing_fields_use_the_validation_envelope(client: TestClient) -> None:
    response = client.post("/api/v1/auth/register", json={"email": EMAIL})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_login_returns_a_bearer_token_pair(client: TestClient) -> None:
    _register(client)
    body = _login(client)

    assert body["token_type"] == "Bearer"  # noqa: S105 - a scheme name, not a credential
    assert body["expires_in"] == 900
    assert body["access_token"] != body["refresh_token"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": EMAIL, "password": "wrong password entirely"},
        {"email": "nobody@example.com", "password": PASSWORD},
    ],
)
def test_invalid_credentials_are_401_with_a_bearer_challenge(
    client: TestClient, payload: dict[str, str]
) -> None:
    _register(client)
    response = client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_the_current_user_endpoint_requires_a_valid_access_token(client: TestClient) -> None:
    _register(client)
    tokens = _login(client)

    me = client.get("/api/v1/users/me", headers=_bearer(tokens["access_token"]))
    assert me.status_code == HTTPStatus.OK
    assert me.json()["email"] == EMAIL
    assert me.json()["last_login_at"] is not None

    anonymous = client.get("/api/v1/users/me")
    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED
    assert anonymous.headers["WWW-Authenticate"] == "Bearer"
    assert anonymous.json()["error"]["code"] == "UNAUTHENTICATED"

    malformed = client.get("/api/v1/users/me", headers=_bearer("not.a.jwt"))
    assert malformed.status_code == HTTPStatus.UNAUTHORIZED
    assert malformed.json()["error"]["code"] == "INVALID_TOKEN"

    wrong_scheme = client.get("/api/v1/users/me", headers={"Authorization": "Basic abc"})
    assert wrong_scheme.status_code == HTTPStatus.UNAUTHORIZED

    as_refresh = client.get("/api/v1/users/me", headers=_bearer(tokens["refresh_token"]))
    assert as_refresh.status_code == HTTPStatus.UNAUTHORIZED
    assert as_refresh.json()["error"]["code"] == "INVALID_TOKEN"


def test_an_expired_access_token_is_rejected_as_expired(
    client: TestClient, settings: ApiSettings, store: InMemoryStore
) -> None:
    _register(client)
    (user,) = store.users.values()
    codec = JwtTokenCodec(
        secret=settings.jwt_secret.get_secret_value(),
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    expired = codec.encode(
        TokenClaims(
            subject=user.id,
            token_id=uuid4(),
            token_type=TokenType.ACCESS,
            issued_at=datetime.now(UTC) - timedelta(hours=1),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )

    response = client.get("/api/v1/users/me", headers=_bearer(expired))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_refresh_rotates_and_reuse_kills_the_session(client: TestClient) -> None:
    _register(client)
    first = _login(client)

    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == HTTPStatus.OK
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get("/api/v1/users/me", headers=_bearer(second["access_token"])).status_code == (
        HTTPStatus.OK
    )

    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reused.status_code == HTTPStatus.UNAUTHORIZED
    assert reused.json()["error"]["code"] == "INVALID_TOKEN"

    collateral = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert collateral.status_code == HTTPStatus.UNAUTHORIZED


def test_invalid_refresh_tokens_are_401(client: TestClient) -> None:
    _register(client)
    tokens = _login(client)

    garbage = client.post("/api/v1/auth/refresh", json={"refresh_token": "garbage"})
    assert garbage.status_code == HTTPStatus.UNAUTHORIZED
    assert garbage.json()["error"]["code"] == "INVALID_TOKEN"

    access_as_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert access_as_refresh.status_code == HTTPStatus.UNAUTHORIZED


def test_logout_revokes_the_session(client: TestClient) -> None:
    _register(client)
    tokens = _login(client)

    logout = client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == HTTPStatus.NO_CONTENT
    again = client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == HTTPStatus.NO_CONTENT

    revoked = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert revoked.status_code == HTTPStatus.UNAUTHORIZED
    assert revoked.json()["error"]["code"] == "INVALID_TOKEN"


def test_suspended_users_are_refused_everywhere(client: TestClient, store: InMemoryStore) -> None:
    _register(client)
    tokens = _login(client)
    (user,) = store.users.values()
    store.users[user.id] = replace(user, status=UserStatus.SUSPENDED)

    for response in (
        client.get("/api/v1/users/me", headers=_bearer(tokens["access_token"])),
        client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}),
        client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}),
    ):
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert response.json()["error"]["code"] == "ACCOUNT_SUSPENDED"


def test_a_token_only_ever_resolves_to_its_own_subject(client: TestClient) -> None:
    _register(client, email="alice@example.com")
    _register(client, email="bob@example.com")
    alice = _login(client, email="alice@example.com")
    bob = _login(client, email="bob@example.com")

    assert client.get("/api/v1/users/me", headers=_bearer(alice["access_token"])).json()[
        "email"
    ] == ("alice@example.com")
    assert client.get("/api/v1/users/me", headers=_bearer(bob["access_token"])).json()["email"] == (
        "bob@example.com"
    )
    # Bob's refresh token never yields Alice's session: rotation stays within one family.
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": bob["refresh_token"]})
    assert rotated.json()["access_token"] != alice["access_token"]


def test_openapi_documents_the_bearer_scheme(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "BearerAccessToken" in document["components"]["securitySchemes"]
    assert document["paths"]["/api/v1/users/me"]["get"]["security"] == [{"BearerAccessToken": []}]
