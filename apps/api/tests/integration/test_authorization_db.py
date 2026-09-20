"""Cross-user access against PostgreSQL: the owner filters are in the SQL, not only in fakes."""

from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value


def _login(client: TestClient, email: str) -> dict[str, str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == HTTPStatus.OK, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_collections_and_conversations_are_isolated_per_user(db_client: TestClient) -> None:
    suffix = uuid4().hex
    alice = _login(db_client, f"alice-{suffix}@example.com")
    bob = _login(db_client, f"bob-{suffix}@example.com")

    collection = db_client.post("/api/v1/collections", json={"name": "Alice's"}, headers=alice)
    assert collection.status_code == HTTPStatus.CREATED
    collection_id = collection.json()["id"]
    conversation = db_client.post(
        "/api/v1/conversations",
        json={"title": "Risks", "collection_id": collection_id},
        headers=alice,
    )
    assert conversation.status_code == HTTPStatus.CREATED
    conversation_id = conversation.json()["id"]

    for method, path, body in (
        ("GET", f"/api/v1/collections/{collection_id}", None),
        ("PATCH", f"/api/v1/collections/{collection_id}", {"name": "Taken"}),
        ("DELETE", f"/api/v1/collections/{collection_id}", None),
        ("GET", f"/api/v1/documents?collection_id={collection_id}", None),
        ("GET", f"/api/v1/conversations/{conversation_id}", None),
        ("PATCH", f"/api/v1/conversations/{conversation_id}", {"title": "Taken"}),
        ("DELETE", f"/api/v1/conversations/{conversation_id}", None),
        ("GET", f"/api/v1/conversations/{conversation_id}/messages", None),
        ("POST", "/api/v1/conversations", {"title": "x", "collection_id": collection_id}),
    ):
        response = db_client.request(method, path, json=body, headers=bob)
        assert response.status_code == HTTPStatus.NOT_FOUND, (method, path, response.text)

    assert db_client.get(f"/api/v1/collections/{collection_id}", headers=alice).json()["name"] == (
        "Alice's"
    )
    assert (
        db_client.get(f"/api/v1/conversations/{conversation_id}", headers=alice).json()["title"]
        == "Risks"
    )
    assert [c["id"] for c in db_client.get("/api/v1/collections", headers=bob).json()] == []
