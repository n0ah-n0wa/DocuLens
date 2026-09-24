"""Collections against a real, migrated PostgreSQL (SPECIFICATIONS.md §30, §45).

The in-memory fake reproduces the detach-on-delete behaviour, but in PostgreSQL that guarantee is
the schema's ``ON DELETE SET NULL`` rule rather than application code. These tests drive the
lifecycle through HTTP against the real database so the two can never silently disagree.
"""

import asyncio
from dataclasses import replace
from http import HTTPStatus
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from doculens.domain.documents import Document
from doculens.infrastructure.config import CoreSettings
from doculens.infrastructure.persistence.database import Database
from doculens.testing.factories import Factories

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value


def _register(client: TestClient, email: str) -> tuple[dict[str, str], UUID]:
    """A fresh account per test: this database is shared across the module, not truncated."""
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == HTTPStatus.OK, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == HTTPStatus.OK, me.text
    return headers, UUID(me.json()["id"])


def _insert_document(
    url: str, owner_id: UUID, collection_id: UUID | None = None, filename: str = "report.pdf"
) -> Document:
    """Insert a document row directly, because uploading is not an endpoint yet (OQ-3)."""
    document = replace(
        Factories.document(owner_id, collection_id),
        filename=filename,
        content_hash=uuid4().hex * 2,
    )

    async def insert() -> None:
        database = Database(CoreSettings(_env_file=None, database_url=SecretStr(url)))
        try:
            async with database.unit_of_work() as uow:
                await uow.documents.add(document)
                await uow.commit()
        finally:
            await database.dispose()

    asyncio.run(insert())
    return document


def _create_collection(client: TestClient, headers: dict[str, str], name: str) -> str:
    response = client.post("/api/v1/collections", json={"name": name}, headers=headers)
    assert response.status_code == HTTPStatus.CREATED, response.text
    return str(response.json()["id"])


def test_the_collection_lifecycle_persists_in_postgresql(db_client: TestClient) -> None:
    headers, _ = _register(db_client, f"lifecycle-{uuid4().hex}@example.com")

    collection_id = _create_collection(db_client, headers, "Contracts")
    path = f"/api/v1/collections/{collection_id}"

    listed = db_client.get("/api/v1/collections", headers=headers)
    assert [c["id"] for c in listed.json()] == [collection_id]

    renamed = db_client.patch(path, json={"name": "Legal", "description": "All"}, headers=headers)
    assert renamed.status_code == HTTPStatus.OK, renamed.text
    assert (renamed.json()["name"], renamed.json()["description"]) == ("Legal", "All")

    reloaded = db_client.get(path, headers=headers)
    assert (reloaded.json()["name"], reloaded.json()["description"]) == ("Legal", "All")

    assert db_client.delete(path, headers=headers).status_code == HTTPStatus.NO_CONTENT
    assert db_client.get(path, headers=headers).status_code == HTTPStatus.NOT_FOUND
    assert db_client.get("/api/v1/collections", headers=headers).json() == []


def test_deleting_a_collection_detaches_documents_and_conversations_in_postgresql(
    db_client: TestClient, migrated_database_url: str
) -> None:
    """The §30 guarantee end to end: the foreign keys null out, the rows survive."""
    headers, owner_id = _register(db_client, f"detach-{uuid4().hex}@example.com")
    collection_id = _create_collection(db_client, headers, "Doomed")
    document = _insert_document(migrated_database_url, owner_id, UUID(collection_id))
    conversation = db_client.post(
        "/api/v1/conversations",
        json={"title": "Risks", "collection_id": collection_id},
        headers=headers,
    )
    assert conversation.status_code == HTTPStatus.CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    deleted = db_client.delete(f"/api/v1/collections/{collection_id}", headers=headers)

    assert deleted.status_code == HTTPStatus.NO_CONTENT
    kept_document = db_client.get(f"/api/v1/documents/{document.id}", headers=headers)
    assert kept_document.status_code == HTTPStatus.OK, "the document outlives its collection"
    assert kept_document.json()["collection_id"] is None
    assert kept_document.json()["filename"] == "report.pdf"

    kept_conversation = db_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
    assert kept_conversation.status_code == HTTPStatus.OK
    assert kept_conversation.json()["collection_id"] is None


def test_documents_are_assigned_removed_and_queried_by_collection_in_postgresql(
    db_client: TestClient, migrated_database_url: str
) -> None:
    headers, owner_id = _register(db_client, f"assign-{uuid4().hex}@example.com")
    source = _create_collection(db_client, headers, "Source")
    target = _create_collection(db_client, headers, "Target")
    document = _insert_document(migrated_database_url, owner_id, UUID(source))
    path = f"/api/v1/documents/{document.id}"

    assert [
        d["id"]
        for d in db_client.get(f"/api/v1/documents?collection_id={source}", headers=headers).json()
    ] == [str(document.id)]

    moved = db_client.patch(path, json={"collection_id": target}, headers=headers)
    assert moved.status_code == HTTPStatus.OK, moved.text
    assert moved.json()["collection_id"] == target
    assert db_client.get(f"/api/v1/documents?collection_id={source}", headers=headers).json() == []

    removed = db_client.patch(path, json={"collection_id": None}, headers=headers)
    assert removed.json()["collection_id"] is None
    assert db_client.get(f"/api/v1/documents?collection_id={target}", headers=headers).json() == []
    assert db_client.get(path, headers=headers).status_code == HTTPStatus.OK, "still the owner's"


def test_querying_an_unknown_collection_is_not_found_in_postgresql(db_client: TestClient) -> None:
    headers, _ = _register(db_client, f"unknown-{uuid4().hex}@example.com")

    response = db_client.get(f"/api/v1/documents?collection_id={uuid4()}", headers=headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["error"]["code"] == "COLLECTION_NOT_FOUND"
