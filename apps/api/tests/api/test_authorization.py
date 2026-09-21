"""Negative authorization tests (SPECIFICATIONS.md §9): every user-owned resource is invisible,
immutable and undeletable for other users, and unreachable without a token."""

from http import HTTPStatus
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from doculens.domain.conversations import Citation, Message, MessageRole
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore

pytestmark = pytest.mark.api

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value


def _login(client: TestClient, email: str) -> dict[str, str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == HTTPStatus.OK, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class Seed:
    """Alice owns one of everything; Bob owns a collection to be used as a foreign target."""

    def __init__(self, client: TestClient, store: InMemoryStore) -> None:
        self.alice = _login(client, "alice@example.com")
        self.bob = _login(client, "bob@example.com")
        alice_id = next(u.id for u in store.users.values() if u.email == "alice@example.com")
        bob_id = next(u.id for u in store.users.values() if u.email == "bob@example.com")

        created = client.post(
            "/api/v1/collections", json={"name": "Alice's contracts"}, headers=self.alice
        )
        self.collection_id = UUID(created.json()["id"])
        self.bob_collection_id = UUID(
            client.post("/api/v1/collections", json={"name": "Bob's"}, headers=self.bob).json()[
                "id"
            ]
        )

        document = Factories.document(alice_id, self.collection_id)
        store.documents[document.id] = document
        self.document_id = document.id

        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Risks", "collection_id": str(self.collection_id)},
            headers=self.alice,
        )
        self.conversation_id = UUID(conversation.json()["id"])
        answer = Message(
            id=new_id(),
            conversation_id=self.conversation_id,
            role=MessageRole.ASSISTANT,
            content="Three risks are identified.",
            created_at=utc_now(),
        )
        store.messages[answer.id] = answer
        citation = Citation(
            id=new_id(),
            message_id=answer.id,
            document_id=document.id,
            page_number=17,
            quoted_text="three main risks",
            retrieval_score=0.87,
            citation_order=0,
        )
        store.citations[citation.id] = citation
        self.message_id = answer.id
        self.bob_id = bob_id


@pytest.fixture
def seed(client: TestClient, store: InMemoryStore) -> Seed:
    return Seed(client, store)


def _requests(seed: Seed) -> list[tuple[str, str, dict[str, object] | None, str]]:
    """(method, path, json, not-found code) for every route that addresses Alice's resources."""
    c, d, v = seed.collection_id, seed.document_id, seed.conversation_id
    return [
        ("GET", f"/api/v1/collections/{c}", None, "COLLECTION_NOT_FOUND"),
        ("PATCH", f"/api/v1/collections/{c}", {"name": "Taken"}, "COLLECTION_NOT_FOUND"),
        ("DELETE", f"/api/v1/collections/{c}", None, "COLLECTION_NOT_FOUND"),
        ("GET", f"/api/v1/documents?collection_id={c}", None, "COLLECTION_NOT_FOUND"),
        ("GET", f"/api/v1/documents/{d}", None, "DOCUMENT_NOT_FOUND"),
        ("PATCH", f"/api/v1/documents/{d}", {"filename": "taken.pdf"}, "DOCUMENT_NOT_FOUND"),
        ("GET", f"/api/v1/conversations/{v}", None, "CONVERSATION_NOT_FOUND"),
        ("PATCH", f"/api/v1/conversations/{v}", {"title": "Taken"}, "CONVERSATION_NOT_FOUND"),
        ("DELETE", f"/api/v1/conversations/{v}", None, "CONVERSATION_NOT_FOUND"),
        ("GET", f"/api/v1/conversations/{v}/messages", None, "CONVERSATION_NOT_FOUND"),
        (
            "POST",
            f"/api/v1/conversations/{v}/messages",
            {"question": "What are the risks?"},
            "CONVERSATION_NOT_FOUND",
        ),
    ]


def test_the_owner_can_reach_everything(client: TestClient, seed: Seed) -> None:
    for method, path, body, _ in _requests(seed):
        if method == "DELETE":
            continue
        response = client.request(method, path, json=body, headers=seed.alice)
        assert response.status_code in (HTTPStatus.OK, HTTPStatus.CREATED), (
            method,
            path,
            response.text,
        )

    messages = client.get(
        f"/api/v1/conversations/{seed.conversation_id}/messages", headers=seed.alice
    ).json()
    # The seeded answer, then the question asked by the sweep and its answer.
    assert messages[0]["id"] == str(seed.message_id)
    assert [m["role"] for m in messages] == ["ASSISTANT", "USER", "ASSISTANT"]
    assert messages[0]["citations"][0]["document_id"] == str(seed.document_id)


def test_another_user_sees_not_found_everywhere_and_changes_nothing(
    client: TestClient, seed: Seed, store: InMemoryStore
) -> None:
    before = (
        dict(store.collections),
        dict(store.documents),
        dict(store.conversations),
        dict(store.messages),
        dict(store.citations),
    )

    for method, path, body, code in _requests(seed):
        response = client.request(method, path, json=body, headers=seed.bob)
        assert response.status_code == HTTPStatus.NOT_FOUND, (method, path, response.text)
        assert response.json()["error"]["code"] == code, (method, path)

    after = (
        dict(store.collections),
        dict(store.documents),
        dict(store.conversations),
        dict(store.messages),
        dict(store.citations),
    )
    assert after == before


def test_listing_never_includes_another_users_resources(client: TestClient, seed: Seed) -> None:
    assert [c["id"] for c in client.get("/api/v1/collections", headers=seed.bob).json()] == [
        str(seed.bob_collection_id)
    ]
    assert client.get("/api/v1/documents", headers=seed.bob).json() == []
    assert client.get("/api/v1/conversations", headers=seed.bob).json() == []


def test_resources_cannot_be_moved_into_another_users_collection(
    client: TestClient, seed: Seed, store: InMemoryStore
) -> None:
    foreign = str(seed.bob_collection_id)

    moved_document = client.patch(
        f"/api/v1/documents/{seed.document_id}", json={"collection_id": foreign}, headers=seed.alice
    )
    moved_conversation = client.patch(
        f"/api/v1/conversations/{seed.conversation_id}",
        json={"collection_id": foreign},
        headers=seed.alice,
    )
    created = client.post(
        "/api/v1/conversations", json={"title": "x", "collection_id": foreign}, headers=seed.alice
    )

    for response in (moved_document, moved_conversation, created):
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["error"]["code"] == "COLLECTION_NOT_FOUND"
    assert store.documents[seed.document_id].collection_id == seed.collection_id
    assert store.conversations[seed.conversation_id].collection_id == seed.collection_id


def test_every_resource_route_requires_authentication(client: TestClient, seed: Seed) -> None:
    routes = [
        *_requests(seed),
        ("GET", "/api/v1/collections", None, ""),
        ("POST", "/api/v1/collections", {"name": "x"}, ""),
        ("GET", "/api/v1/documents", None, ""),
        ("GET", "/api/v1/conversations", None, ""),
        ("POST", "/api/v1/conversations", {"title": "x"}, ""),
    ]
    for method, path, body, _ in routes:
        anonymous = client.request(method, path, json=body)
        assert anonymous.status_code == HTTPStatus.UNAUTHORIZED, (method, path)
        assert anonymous.json()["error"]["code"] == "UNAUTHENTICATED"
        assert anonymous.headers["WWW-Authenticate"] == "Bearer"


def test_unknown_and_foreign_identifiers_are_indistinguishable(
    client: TestClient, seed: Seed
) -> None:
    foreign = client.get(f"/api/v1/documents/{seed.document_id}", headers=seed.bob)
    unknown = client.get(f"/api/v1/documents/{uuid4()}", headers=seed.bob)

    assert foreign.status_code == unknown.status_code == HTTPStatus.NOT_FOUND
    assert foreign.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert foreign.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_every_documented_route_except_probes_and_auth_is_protected(client: TestClient) -> None:
    """Guards future routes: anything outside /health and /api/v1/auth must declare bearer auth."""
    document = client.get("/openapi.json").json()
    unprotected = [
        f"{method.upper()} {path}"
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        if not path.startswith(("/health", "/api/v1/auth")) and not operation.get("security")
    ]

    assert unprotected == []
