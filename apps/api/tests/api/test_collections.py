"""Collections over HTTP (SPECIFICATIONS.md §9, §29, §30, §33): create, list, inspect, rename,
delete, assign and remove documents, and list the documents of one collection.

Deleting a collection detaches the documents and conversations it grouped; it never deletes them
(§30). Cross-user and anonymous access to these routes is covered by ``test_authorization.py``,
which also proves that a rejected request changes nothing; the tests here pin the owner's
behaviour, the validation rules and that detach guarantee.
"""

from dataclasses import replace
from http import HTTPStatus
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from doculens.domain.documents import Document
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore

pytestmark = pytest.mark.api

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value
EMAIL = "owner@example.com"


class Owner:
    """A registered, logged-in user and the identifier its rows are seeded under."""

    def __init__(self, client: TestClient, store: InMemoryStore) -> None:
        client.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
        login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == HTTPStatus.OK, login.text
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.id = next(user.id for user in store.users.values() if user.email == EMAIL)


@pytest.fixture
def owner(client: TestClient, store: InMemoryStore) -> Owner:
    return Owner(client, store)


def _create(
    client: TestClient, owner: Owner, name: str = "Contracts", **fields: object
) -> dict[str, object]:
    response = client.post(
        "/api/v1/collections", json={"name": name, **fields}, headers=owner.headers
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return dict(response.json())


def _seed_document(
    store: InMemoryStore,
    owner: Owner,
    collection_id: UUID | None = None,
    filename: str = "report.pdf",
) -> Document:
    """A document row for the owner; uploading is not an endpoint yet (OQ-3), so rows are seeded.

    ``content_hash`` is unique per document because live documents are unique per
    ``(owner_id, content_hash)``.
    """
    document = replace(
        Factories.document(owner.id, collection_id),
        filename=filename,
        content_hash=uuid4().hex * 2,
    )
    store.documents[document.id] = document
    return document


def test_a_collection_is_created_then_listed_and_inspected(
    client: TestClient, owner: Owner
) -> None:
    created = _create(client, owner, name="  Contracts  ", description="  Signed PDFs  ")

    assert created["name"] == "Contracts", "the name is trimmed"
    assert created["description"] == "Signed PDFs"
    assert created["created_at"] == created["updated_at"]

    listed = client.get("/api/v1/collections", headers=owner.headers)
    fetched = client.get(f"/api/v1/collections/{created['id']}", headers=owner.headers)

    assert listed.status_code == fetched.status_code == HTTPStatus.OK
    assert listed.json() == [created]
    assert fetched.json() == created


def test_a_collection_never_exposes_the_owner_identifier(client: TestClient, owner: Owner) -> None:
    assert "owner_id" not in _create(client, owner)


def test_a_blank_description_is_stored_as_absent(client: TestClient, owner: Owner) -> None:
    assert _create(client, owner, description="   ")["description"] is None
    assert _create(client, owner, description=None)["description"] is None


@pytest.mark.parametrize(
    ("name", "status", "code"),
    [
        ("", HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR"),
        ("x" * 201, HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR"),
        ("   ", HTTPStatus.BAD_REQUEST, "INVALID_INPUT"),
    ],
)
def test_invalid_collection_names_are_rejected(
    client: TestClient, owner: Owner, name: str, status: HTTPStatus, code: str
) -> None:
    response = client.post("/api/v1/collections", json={"name": name}, headers=owner.headers)

    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert client.get("/api/v1/collections", headers=owner.headers).json() == []


def test_a_collection_is_renamed_and_its_description_set_then_cleared(
    client: TestClient, owner: Owner
) -> None:
    collection_id = _create(client, owner, description="Signed PDFs")["id"]
    path = f"/api/v1/collections/{collection_id}"

    renamed = client.patch(path, json={"name": " Legal "}, headers=owner.headers)
    cleared = client.patch(path, json={"description": None}, headers=owner.headers)
    untouched = client.patch(path, json={}, headers=owner.headers)

    assert renamed.status_code == HTTPStatus.OK, renamed.text
    assert renamed.json()["name"] == "Legal", "the new name is trimmed"
    assert renamed.json()["description"] == "Signed PDFs", "an omitted field is unchanged"
    assert cleared.json()["description"] is None, "an explicit null clears the description"
    assert cleared.json()["name"] == "Legal", "clearing the description keeps the name"
    assert (untouched.json()["name"], untouched.json()["description"]) == ("Legal", None)


def test_renaming_to_an_invalid_name_leaves_the_collection_unchanged(
    client: TestClient, owner: Owner
) -> None:
    collection = _create(client, owner)
    path = f"/api/v1/collections/{collection['id']}"

    rejected = client.patch(path, json={"name": "   "}, headers=owner.headers)

    assert rejected.status_code == HTTPStatus.BAD_REQUEST
    assert rejected.json()["error"]["code"] == "INVALID_INPUT"
    assert client.get(path, headers=owner.headers).json() == collection


def test_collections_may_share_a_name_and_are_listed_in_creation_order(
    client: TestClient, owner: Owner
) -> None:
    first = _create(client, owner, name="Contracts")
    second = _create(client, owner, name="Contracts")

    listed = client.get("/api/v1/collections", headers=owner.headers).json()

    assert first["id"] != second["id"], "the name is not an identifier"
    assert [c["id"] for c in listed] == [first["id"], second["id"]]


def test_deleting_a_collection_keeps_its_documents_and_conversations(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    """The §30 guarantee: the grouping disappears, the grouped resources survive, detached."""
    collection_id = str(_create(client, owner)["id"])
    document = _seed_document(store, owner, UUID(collection_id))
    conversation = client.post(
        "/api/v1/conversations",
        json={"title": "Risks", "collection_id": collection_id},
        headers=owner.headers,
    ).json()

    deleted = client.delete(f"/api/v1/collections/{collection_id}", headers=owner.headers)

    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert deleted.content == b""
    gone = client.get(f"/api/v1/collections/{collection_id}", headers=owner.headers)
    assert gone.status_code == HTTPStatus.NOT_FOUND
    assert gone.json()["error"]["code"] == "COLLECTION_NOT_FOUND"

    kept_document = client.get(f"/api/v1/documents/{document.id}", headers=owner.headers)
    assert kept_document.status_code == HTTPStatus.OK, "the document outlives its collection"
    assert kept_document.json()["collection_id"] is None
    assert [d["id"] for d in client.get("/api/v1/documents", headers=owner.headers).json()] == [
        str(document.id)
    ]

    kept_conversation = client.get(
        f"/api/v1/conversations/{conversation['id']}", headers=owner.headers
    )
    assert kept_conversation.status_code == HTTPStatus.OK
    assert kept_conversation.json()["collection_id"] is None


def test_deleting_a_collection_leaves_documents_in_other_collections_alone(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    doomed = str(_create(client, owner, name="Doomed")["id"])
    kept = str(_create(client, owner, name="Kept")["id"])
    detached = _seed_document(store, owner, UUID(doomed), filename="detached.pdf")
    untouched = _seed_document(store, owner, UUID(kept), filename="untouched.pdf")

    client.delete(f"/api/v1/collections/{doomed}", headers=owner.headers)

    assert (
        client.get(f"/api/v1/documents/{detached.id}", headers=owner.headers).json()[
            "collection_id"
        ]
        is None
    )
    assert (
        client.get(f"/api/v1/documents/{untouched.id}", headers=owner.headers).json()[
            "collection_id"
        ]
        == kept
    )


def test_deleting_a_collection_twice_reports_not_found(client: TestClient, owner: Owner) -> None:
    path = f"/api/v1/collections/{_create(client, owner)['id']}"

    assert client.delete(path, headers=owner.headers).status_code == HTTPStatus.NO_CONTENT
    repeated = client.delete(path, headers=owner.headers)
    assert repeated.status_code == HTTPStatus.NOT_FOUND
    assert repeated.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


def test_a_document_is_assigned_to_a_collection_then_removed_from_it(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    collection_id = str(_create(client, owner)["id"])
    document = _seed_document(store, owner)
    path = f"/api/v1/documents/{document.id}"

    assigned = client.patch(path, json={"collection_id": collection_id}, headers=owner.headers)
    assert assigned.status_code == HTTPStatus.OK, assigned.text
    assert assigned.json()["collection_id"] == collection_id

    removed = client.patch(path, json={"collection_id": None}, headers=owner.headers)
    assert removed.status_code == HTTPStatus.OK
    assert removed.json()["collection_id"] is None, "the document is unfiled, not deleted"
    assert client.get(path, headers=owner.headers).status_code == HTTPStatus.OK


def test_a_document_moves_directly_between_collections(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    source = str(_create(client, owner, name="Source")["id"])
    target = str(_create(client, owner, name="Target")["id"])
    document = _seed_document(store, owner, UUID(source))

    moved = client.patch(
        f"/api/v1/documents/{document.id}",
        json={"collection_id": target},
        headers=owner.headers,
    )

    assert moved.json()["collection_id"] == target
    assert (
        client.get(f"/api/v1/documents?collection_id={source}", headers=owner.headers).json() == []
    )
    assert [
        d["id"]
        for d in client.get(
            f"/api/v1/documents?collection_id={target}", headers=owner.headers
        ).json()
    ] == [str(document.id)]


def test_a_collection_is_queried_for_its_own_documents_only(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    collection_id = str(_create(client, owner)["id"])
    inside = _seed_document(store, owner, UUID(collection_id), filename="inside.pdf")
    outside = _seed_document(store, owner, filename="outside.pdf")

    in_collection = client.get(
        f"/api/v1/documents?collection_id={collection_id}", headers=owner.headers
    )
    everything = client.get("/api/v1/documents", headers=owner.headers)

    assert in_collection.status_code == HTTPStatus.OK
    assert [d["id"] for d in in_collection.json()] == [str(inside.id)]
    assert {d["id"] for d in everything.json()} == {str(inside.id), str(outside.id)}


def test_an_empty_collection_is_queried_successfully(client: TestClient, owner: Owner) -> None:
    collection_id = _create(client, owner)["id"]

    response = client.get(f"/api/v1/documents?collection_id={collection_id}", headers=owner.headers)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == []


def test_querying_an_unknown_collection_is_not_found(client: TestClient, owner: Owner) -> None:
    response = client.get(f"/api/v1/documents?collection_id={uuid4()}", headers=owner.headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


def test_assigning_a_document_to_an_unknown_collection_is_not_found(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    document = _seed_document(store, owner)

    response = client.patch(
        f"/api/v1/documents/{document.id}",
        json={"collection_id": str(uuid4())},
        headers=owner.headers,
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["error"]["code"] == "COLLECTION_NOT_FOUND"
    assert store.documents[document.id].collection_id is None, "the document is unchanged"
