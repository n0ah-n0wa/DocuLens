"""Document lifecycle against PostgreSQL, filesystem storage and the in-memory vector store."""

import asyncio
from dataclasses import replace
from http import HTTPStatus
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from doculens.domain.chunking import chunk_id
from doculens.domain.conversations import Citation, Message, MessageRole
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.ids import new_id
from doculens.domain.storage import PDF_MIME_TYPE, document_object_key
from doculens.domain.time import utc_now
from doculens.domain.vectors import VectorMetadata, VectorRecord, vector_id_for
from doculens.infrastructure.persistence.database import Database
from doculens.testing.factories import Factories
from doculens_api.dependencies import AppComponents

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"  # noqa: S105 - test-only value


def _register(client: TestClient, email: str) -> tuple[dict[str, str], UUID]:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == HTTPStatus.OK, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/api/v1/users/me", headers=headers)
    return headers, UUID(me.json()["id"])


def _components(client: TestClient) -> AppComponents:
    app = client.app
    assert isinstance(app, FastAPI)
    return cast("AppComponents", app.state.components)


def _database(client: TestClient) -> Database:
    """A new engine on the caller's event loop; the app's engine belongs to TestClient."""
    return Database(_components(client).settings)


def _insert_ready(client: TestClient, owner_id: UUID, filename: str = "report.pdf") -> Document:
    components = _components(client)
    document = replace(
        Factories.document(owner_id),
        filename=filename,
        processing_status=ProcessingStatus.READY,
        page_count=1,
        chunk_count=1,
        content_hash=uuid4().hex * 2,
        metadata={"pdf": {"title": filename}},
    )
    document = replace(document, storage_key=document_object_key(owner_id, document.id))
    page = DocumentPage(
        id=new_id(),
        document_id=document.id,
        page_number=1,
        extracted_text="notice period is thirty days",
        character_count=28,
        metadata={"has_text": True},
    )
    chunk = DocumentChunk(
        id=chunk_id(document.id, 0),
        document_id=document.id,
        page_id=page.id,
        chunk_index=0,
        text=page.extracted_text,
        token_count=5,
        metadata={"page_number": 1},
        vector_id=str(chunk_id(document.id, 0)),
    )

    async def persist() -> None:
        await components.object_storage.put(
            document.storage_key, b"%PDF-1.4 original", content_type=PDF_MIME_TYPE
        )
        await components.vector_store.upsert(
            owner_id,
            [
                VectorRecord(
                    id=vector_id_for(chunk.id),
                    vector=(1.0, 0.0),
                    metadata=VectorMetadata(
                        user_id=owner_id,
                        document_id=document.id,
                        chunk_id=chunk.id,
                        chunk_index=0,
                        page_number=1,
                    ),
                )
            ],
        )
        database = _database(client)
        try:
            async with database.unit_of_work() as uow:
                await uow.documents.add(document)
                await uow.document_content.add_pages([page])
                await uow.document_content.add_chunks([chunk])
                await uow.commit()
        finally:
            await database.dispose()

    asyncio.run(persist())
    return document


def test_search_rename_and_metadata_round_trip_in_postgresql(db_client: TestClient) -> None:
    headers, owner_id = _register(db_client, f"search-{uuid4().hex}@example.com")
    report = _insert_ready(db_client, owner_id, filename="Q3-report.pdf")
    _insert_ready(db_client, owner_id, filename="notes.pdf")

    searched = db_client.get("/api/v1/documents?q=q3", headers=headers)
    assert [d["id"] for d in searched.json()] == [str(report.id)]
    assert searched.json()[0]["metadata"] == {"pdf": {"title": "Q3-report.pdf"}}

    renamed = db_client.patch(
        f"/api/v1/documents/{report.id}", json={"filename": "annual.pdf"}, headers=headers
    )
    assert renamed.status_code == HTTPStatus.OK
    assert db_client.get(f"/api/v1/documents/{report.id}", headers=headers).json()["filename"] == (
        "annual.pdf"
    )


def test_delete_purges_postgres_object_store_and_vectors(db_client: TestClient) -> None:
    headers, owner_id = _register(db_client, f"delete-{uuid4().hex}@example.com")
    document = _insert_ready(db_client, owner_id)
    components = _components(db_client)

    async def cite() -> None:
        conversation = Factories.conversation(owner_id)
        answer = Message(
            id=new_id(),
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="cited",
            created_at=utc_now(),
        )
        database = _database(db_client)
        try:
            async with database.unit_of_work() as uow:
                pages = await uow.document_content.list_pages(owner_id, document.id)
                chunks = await uow.document_content.list_chunks(owner_id, document.id)
                await uow.conversations.add(conversation)
                await uow.messages.add(
                    owner_id,
                    answer,
                    [
                        Citation(
                            id=new_id(),
                            message_id=answer.id,
                            document_id=document.id,
                            page_number=1,
                            quoted_text="thirty days",
                            retrieval_score=0.8,
                            citation_order=0,
                            chunk_id=chunks[0].id,
                        )
                    ],
                )
                await uow.commit()
                assert pages
        finally:
            await database.dispose()

    asyncio.run(cite())

    first = db_client.delete(f"/api/v1/documents/{document.id}", headers=headers)
    second = db_client.delete(f"/api/v1/documents/{document.id}", headers=headers)
    assert first.status_code == second.status_code == HTTPStatus.NO_CONTENT
    assert db_client.get(f"/api/v1/documents/{document.id}", headers=headers).status_code == (
        HTTPStatus.NOT_FOUND
    )

    async def leftover() -> None:
        assert await components.object_storage.head(document.storage_key) is None
        assert await components.vector_store.count(owner_id, document.id) == 0
        database = _database(db_client)
        try:
            async with database.unit_of_work() as uow:
                tombstone = await uow.documents.get(owner_id, document.id)
                assert tombstone is not None
                assert tombstone.is_deleted
                assert await uow.document_content.list_pages(owner_id, document.id) == []
                assert await uow.document_content.list_chunks(owner_id, document.id) == []
        finally:
            await database.dispose()

    asyncio.run(leftover())


def test_reprocess_and_reindex_persist_the_new_status(db_client: TestClient) -> None:
    headers, owner_id = _register(db_client, f"restart-{uuid4().hex}@example.com")
    document = _insert_ready(db_client, owner_id)

    reprocessed = db_client.post(f"/api/v1/documents/{document.id}/reprocess", headers=headers)
    assert reprocessed.status_code == HTTPStatus.OK
    assert reprocessed.json()["processing_status"] == "VALIDATING"

    # Put it back to READY with pages still present so re-index can start from CHUNKING.
    async def restore() -> None:
        database = _database(db_client)
        try:
            async with database.unit_of_work() as uow:
                current = await uow.documents.get(owner_id, document.id)
                assert current is not None
                await uow.documents.update(
                    replace(current, processing_status=ProcessingStatus.READY)
                )
                await uow.commit()
        finally:
            await database.dispose()

    asyncio.run(restore())
    reindexed = db_client.post(f"/api/v1/documents/{document.id}/reindex", headers=headers)
    assert reindexed.json()["processing_status"] == "CHUNKING"
