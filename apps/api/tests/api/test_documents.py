"""Document lifecycle over HTTP (SPECIFICATIONS.md §9, §29, §31, §32, §33)."""

import asyncio
from dataclasses import replace
from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from doculens.domain.chunking import chunk_id
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.ids import new_id
from doculens.domain.storage import PDF_MIME_TYPE, document_object_key
from doculens.domain.vectors import VectorMetadata, VectorRecord, vector_id_for
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.pdfs import pdf_with_pages
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

if TYPE_CHECKING:
    from doculens_api.dependencies import AppComponents

pytestmark = pytest.mark.api

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value
EMAIL = "owner@example.com"


class Owner:
    def __init__(self, client: TestClient, store: InMemoryStore) -> None:
        client.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
        login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == HTTPStatus.OK, login.text
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.id = next(user.id for user in store.users.values() if user.email == EMAIL)


@pytest.fixture
def owner(client: TestClient, store: InMemoryStore) -> Owner:
    return Owner(client, store)


@pytest.fixture
def sample_pdf() -> bytes:
    return pdf_with_pages(["hello from upload"])


def _page(document_id: UUID, text: str = "evidence") -> DocumentPage:
    return DocumentPage(
        id=new_id(),
        document_id=document_id,
        page_number=1,
        extracted_text=text,
        character_count=len(text),
        metadata={"has_text": True},
    )


def _chunk(document_id: UUID, page_id: UUID) -> DocumentChunk:
    ident = chunk_id(document_id, 0)
    return DocumentChunk(
        id=ident,
        document_id=document_id,
        page_id=page_id,
        chunk_index=0,
        text="evidence",
        token_count=1,
        metadata={"page_number": 1},
        vector_id=str(ident),
    )


async def _put_original(app: FastAPI, document: Document) -> None:
    components: AppComponents = app.state.components
    await components.object_storage.put(
        document.storage_key, b"%PDF-1.4 test", content_type=PDF_MIME_TYPE
    )


async def _index_vector(app: FastAPI, document: Document, chunk: DocumentChunk) -> None:
    components: AppComponents = app.state.components
    await components.vector_store.upsert(
        document.owner_id,
        [
            VectorRecord(
                id=vector_id_for(chunk.id),
                vector=(1.0, 0.0),
                metadata=VectorMetadata(
                    user_id=document.owner_id,
                    document_id=document.id,
                    chunk_id=chunk.id,
                    chunk_index=0,
                    page_number=1,
                    collection_id=document.collection_id,
                ),
            )
        ],
    )


def _seed(
    store: InMemoryStore,
    owner: Owner,
    *,
    filename: str = "report.pdf",
    status: ProcessingStatus = ProcessingStatus.UPLOADED,
    with_pages: bool = False,
) -> Document:
    document = replace(
        Factories.document(owner.id),
        filename=filename,
        processing_status=status,
        content_hash=uuid4().hex * 2,
        metadata={"pdf": {"title": "Q3"}},
    )
    document = replace(document, storage_key=document_object_key(owner.id, document.id))
    store.documents[document.id] = document
    if with_pages:
        page = _page(document.id)
        store.pages[page.id] = page
        chunk = _chunk(document.id, page.id)
        store.chunks[chunk.id] = chunk
    return document


def _upload(
    client: TestClient,
    owner: Owner,
    data: bytes,
    *,
    filename: str = "report.pdf",
    content_type: str = PDF_MIME_TYPE,
    collection_id: UUID | None = None,
) -> Response:
    form: dict[str, str] = {}
    if collection_id is not None:
        form["collection_id"] = str(collection_id)
    return client.post(
        "/api/v1/documents",
        headers=owner.headers,
        files={"file": (filename, data, content_type)},
        data=form,
    )


def test_upload_creates_an_uploaded_document(
    client: TestClient, owner: Owner, store: InMemoryStore, app: FastAPI, sample_pdf: bytes
) -> None:
    collection_id = UUID(
        client.post("/api/v1/collections", json={"name": "Legal"}, headers=owner.headers).json()[
            "id"
        ]
    )

    response = _upload(client, owner, sample_pdf, collection_id=collection_id)

    assert response.status_code == HTTPStatus.CREATED, response.text
    body = response.json()
    assert body["filename"] == "report.pdf"
    assert body["mime_type"] == PDF_MIME_TYPE
    assert body["file_size"] == len(sample_pdf)
    assert body["processing_status"] == "UPLOADED"
    assert body["collection_id"] == str(collection_id)
    assert "storage_key" not in body
    assert "content_hash" not in body
    assert "owner_id" not in body

    document = store.documents[UUID(body["id"])]
    assert document.owner_id == owner.id
    assert document.processing_status is ProcessingStatus.UPLOADED
    components: AppComponents = app.state.components
    stored = asyncio.run(components.object_storage.get(document.storage_key))
    assert stored.data == sample_pdf


def test_upload_rejects_non_pdf_and_duplicates(
    client: TestClient, owner: Owner, sample_pdf: bytes
) -> None:
    refused_type = _upload(
        client, owner, sample_pdf, filename="notes.txt", content_type="text/plain"
    )
    assert refused_type.status_code == HTTPStatus.BAD_REQUEST
    assert refused_type.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    refused_sig = _upload(client, owner, b"not a pdf", filename="fake.pdf")
    assert refused_sig.status_code == HTTPStatus.BAD_REQUEST
    assert refused_sig.json()["error"]["code"] == "INVALID_FILE_SIGNATURE"

    first = _upload(client, owner, sample_pdf)
    assert first.status_code == HTTPStatus.CREATED
    duplicate = _upload(client, owner, sample_pdf, filename="copy.pdf")
    assert duplicate.status_code == HTTPStatus.CONFLICT
    assert duplicate.json()["error"]["code"] == "DUPLICATE_DOCUMENT"
    assert duplicate.json()["error"]["details"][0]["location"] == "existing_document_id"
    assert duplicate.json()["error"]["details"][0]["message"] == first.json()["id"]

    client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": PASSWORD})
    login = client.post(
        "/api/v1/auth/login", json={"email": "other@example.com", "password": PASSWORD}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    other = client.post(
        "/api/v1/documents",
        headers=other_headers,
        files={"file": ("report.pdf", sample_pdf, PDF_MIME_TYPE)},
    )
    assert other.status_code == HTTPStatus.CREATED


def test_upload_into_a_foreign_collection_is_not_found(
    client: TestClient, owner: Owner, sample_pdf: bytes
) -> None:
    client.post("/api/v1/auth/register", json={"email": "bob@example.com", "password": PASSWORD})
    login = client.post(
        "/api/v1/auth/login", json={"email": "bob@example.com", "password": PASSWORD}
    )
    bob = {"Authorization": f"Bearer {login.json()['access_token']}"}
    bob_collection = client.post("/api/v1/collections", json={"name": "Bob's"}, headers=bob).json()[
        "id"
    ]

    response = _upload(client, owner, sample_pdf, collection_id=UUID(bob_collection))
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["error"]["code"] == "COLLECTION_NOT_FOUND"


def test_upload_rejects_oversize_files(
    settings: ApiSettings, store: InMemoryStore, sample_pdf: bytes
) -> None:
    tight = settings.model_copy(update={"max_file_size_mb": 1})
    app = create_app(tight, probes=[], unit_of_work_factory=lambda: InMemoryUnitOfWork(store))
    with TestClient(app) as client:
        owner = Owner(client, store)
        oversized = sample_pdf + b"x" * (2 * 1024 * 1024)
        response = _upload(client, owner, oversized)
    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_documents_are_listed_inspected_renamed_moved_and_searched(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    collection_id = client.post(
        "/api/v1/collections", json={"name": "Legal"}, headers=owner.headers
    ).json()["id"]
    report = _seed(store, owner, filename="Q3-report.pdf")
    notes = _seed(store, owner, filename="notes.pdf")

    listed = client.get("/api/v1/documents", headers=owner.headers)
    assert {d["id"] for d in listed.json()} == {str(report.id), str(notes.id)}

    searched = client.get("/api/v1/documents?q=q3", headers=owner.headers)
    assert [d["id"] for d in searched.json()] == [str(report.id)]
    assert client.get("/api/v1/documents?q=%", headers=owner.headers).json() == []

    inspected = client.get(f"/api/v1/documents/{report.id}", headers=owner.headers)
    assert inspected.status_code == HTTPStatus.OK
    assert inspected.json()["filename"] == "Q3-report.pdf"
    assert inspected.json()["metadata"] == {"pdf": {"title": "Q3"}}
    assert "storage_key" not in inspected.json()
    assert "content_hash" not in inspected.json()
    assert "owner_id" not in inspected.json()

    renamed = client.patch(
        f"/api/v1/documents/{report.id}", json={"filename": " annual.pdf "}, headers=owner.headers
    )
    assert renamed.json()["filename"] == "annual.pdf"

    moved = client.patch(
        f"/api/v1/documents/{report.id}",
        json={"collection_id": collection_id},
        headers=owner.headers,
    )
    assert moved.json()["collection_id"] == collection_id
    scoped = client.get(f"/api/v1/documents?collection_id={collection_id}", headers=owner.headers)
    assert [d["id"] for d in scoped.json()] == [str(report.id)]


def test_delete_is_idempotent_and_hides_the_document(
    client: TestClient, owner: Owner, store: InMemoryStore, app: FastAPI
) -> None:
    document = _seed(store, owner, status=ProcessingStatus.READY, with_pages=True)
    chunk = next(iter(store.chunks.values()))

    asyncio.run(_put_original(app, document))
    asyncio.run(_index_vector(app, document, chunk))

    first = client.delete(f"/api/v1/documents/{document.id}", headers=owner.headers)
    second = client.delete(f"/api/v1/documents/{document.id}", headers=owner.headers)
    assert first.status_code == second.status_code == HTTPStatus.NO_CONTENT

    assert client.get(f"/api/v1/documents/{document.id}", headers=owner.headers).status_code == (
        HTTPStatus.NOT_FOUND
    )
    assert client.get("/api/v1/documents", headers=owner.headers).json() == []
    assert store.documents[document.id].processing_status is ProcessingStatus.DELETED
    assert store.pages == {}
    assert store.chunks == {}


def test_reprocess_and_reindex_change_status_over_http(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    ready = _seed(store, owner, status=ProcessingStatus.READY, with_pages=True)
    uploaded = _seed(store, owner, filename="new.pdf")

    reprocessed = client.post(f"/api/v1/documents/{ready.id}/reprocess", headers=owner.headers)
    assert reprocessed.status_code == HTTPStatus.OK
    assert reprocessed.json()["processing_status"] == "VALIDATING"

    store.documents[ready.id] = replace(
        store.documents[ready.id], processing_status=ProcessingStatus.READY
    )
    reindexed = client.post(f"/api/v1/documents/{ready.id}/reindex", headers=owner.headers)
    assert reindexed.json()["processing_status"] == "CHUNKING"

    refused = client.post(f"/api/v1/documents/{uploaded.id}/reindex", headers=owner.headers)
    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["error"]["code"] == "DOCUMENT_CANNOT_REINDEX"


def test_unknown_and_foreign_lifecycle_routes_are_not_found(
    client: TestClient, owner: Owner, store: InMemoryStore
) -> None:
    other = client.post(
        "/api/v1/auth/register", json={"email": "b@example.com", "password": PASSWORD}
    )
    assert other.status_code == HTTPStatus.CREATED
    login = client.post("/api/v1/auth/login", json={"email": "b@example.com", "password": PASSWORD})
    bob = {"Authorization": f"Bearer {login.json()['access_token']}"}
    document = _seed(store, owner, status=ProcessingStatus.READY, with_pages=True)

    for method, path in (
        ("DELETE", f"/api/v1/documents/{document.id}"),
        ("POST", f"/api/v1/documents/{document.id}/reprocess"),
        ("POST", f"/api/v1/documents/{document.id}/reindex"),
        ("DELETE", f"/api/v1/documents/{uuid4()}"),
    ):
        response = client.request(method, path, headers=bob)
        assert response.status_code == HTTPStatus.NOT_FOUND, path
        assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
