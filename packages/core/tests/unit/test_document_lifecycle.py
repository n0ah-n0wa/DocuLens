"""Document lifecycle: search, delete saga, reprocess and re-index (§29 to §32)."""

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from doculens.application.documents import DocumentService
from doculens.application.storage import ObjectStorage
from doculens.application.vectors import VectorStore
from doculens.domain.chunking import chunk_id
from doculens.domain.conversations import Citation, Message, MessageRole
from doculens.domain.documents import (
    Document,
    DocumentCannotReindexError,
    DocumentChunk,
    DocumentDeletionInProgressError,
    DocumentNotFoundError,
    DocumentPage,
    InvalidStatusTransitionError,
    ProcessingStatus,
)
from doculens.domain.ids import new_id
from doculens.domain.storage import PDF_MIME_TYPE, StorageUnavailableError, document_object_key
from doculens.domain.time import utc_now
from doculens.domain.vectors import (
    VectorMetadata,
    VectorRecord,
    VectorStoreUnavailableError,
    vector_id_for,
)
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryDocumentRepository, InMemoryStore, InMemoryUnitOfWork
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

ALICE = uuid4()
BOB = uuid4()


class _FailingVectors(InMemoryVectorStore):
    """An in-memory store whose document delete can be failed on demand."""

    def __init__(self) -> None:
        super().__init__()
        self.fail = True
        self.delete_calls = 0

    async def delete_document(self, owner_id: UUID, document_id: UUID) -> int:
        self.delete_calls += 1
        if self.fail:
            raise VectorStoreUnavailableError
        return await super().delete_document(owner_id, document_id)


class _FailingStorage(FilesystemObjectStorage):
    """A filesystem store whose delete can be failed on demand."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.fail = True
        self.delete_calls = 0

    async def delete(self, key: str) -> None:
        self.delete_calls += 1
        if self.fail:
            raise StorageUnavailableError
        await super().delete(key)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemObjectStorage:
    return FilesystemObjectStorage(tmp_path / "objects")


@pytest.fixture
def vectors() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@pytest.fixture
def service(
    store: InMemoryStore, storage: FilesystemObjectStorage, vectors: InMemoryVectorStore
) -> DocumentService:
    return DocumentService(
        unit_of_work=lambda: InMemoryUnitOfWork(store), storage=storage, vectors=vectors
    )


def _page(document_id: UUID, number: int = 1, text: str = "evidence") -> DocumentPage:
    return DocumentPage(
        id=new_id(),
        document_id=document_id,
        page_number=number,
        extracted_text=text,
        character_count=len(text),
        metadata={"has_text": True},
    )


def _chunk(document: Document, page: DocumentPage, index: int = 0) -> DocumentChunk:
    ident = chunk_id(document.id, index)
    return DocumentChunk(
        id=ident,
        document_id=document.id,
        page_id=page.id,
        chunk_index=index,
        text=page.extracted_text,
        token_count=1,
        metadata={"page_number": page.page_number, "content_hash": "h1"},
        vector_id=str(ident),
    )


async def _seed_ready(
    store: InMemoryStore,
    storage: ObjectStorage,
    vectors: VectorStore,
    *,
    owner_id: UUID = ALICE,
    filename: str = "report.pdf",
    collection_id: UUID | None = None,
) -> Document:
    document = replace(
        Factories.document(owner_id, collection_id),
        filename=filename,
        processing_status=ProcessingStatus.READY,
        page_count=1,
        chunk_count=1,
        content_hash=uuid4().hex * 2,
    )
    document = replace(document, storage_key=document_object_key(owner_id, document.id))
    store.documents[document.id] = document
    page = _page(document.id)
    chunk = _chunk(document, page)
    store.pages[page.id] = page
    store.chunks[chunk.id] = chunk
    await storage.put(document.storage_key, b"%PDF-1.4 seeded", content_type=PDF_MIME_TYPE)
    await vectors.upsert(
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
                    collection_id=collection_id,
                ),
            )
        ],
    )
    return document


async def test_filename_search_is_literal_and_owner_scoped(
    store: InMemoryStore, service: DocumentService
) -> None:
    mine = Factories.document(ALICE)
    mine = replace(mine, filename="Q3-report.pdf", content_hash="a" * 64)
    other = replace(Factories.document(ALICE), filename="notes.pdf", content_hash="b" * 64)
    foreign = replace(Factories.document(BOB), filename="Q3-report.pdf", content_hash="c" * 64)
    store.documents[mine.id] = mine
    store.documents[other.id] = other
    store.documents[foreign.id] = foreign

    found = await service.list_for_owner(ALICE, query="q3")
    assert [d.id for d in found] == [mine.id]
    percent = await service.list_for_owner(ALICE, query="%")
    assert percent == [], "LIKE wildcards are matched literally"
    assert await service.list_for_owner(BOB, query="q3") == [foreign]
    assert await service.list_for_owner(ALICE, query="   ") == [mine, other]


async def test_delete_purges_every_store_and_is_idempotent(
    store: InMemoryStore,
    storage: FilesystemObjectStorage,
    vectors: InMemoryVectorStore,
    service: DocumentService,
) -> None:
    document = await _seed_ready(store, storage, vectors)
    conversation = Factories.conversation(ALICE)
    store.conversations[conversation.id] = conversation
    answer = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="cited",
        created_at=utc_now(),
    )
    store.messages[answer.id] = answer
    chunk = next(iter(store.chunks.values()))
    citation = Citation(
        id=new_id(),
        message_id=answer.id,
        document_id=document.id,
        page_number=1,
        quoted_text="evidence",
        retrieval_score=0.9,
        citation_order=0,
        chunk_id=chunk.id,
    )
    store.citations[citation.id] = citation

    await service.delete(ALICE, document.id)
    await service.delete(ALICE, document.id)

    with pytest.raises(DocumentNotFoundError):
        await service.get(ALICE, document.id)
    assert await service.list_for_owner(ALICE) == []
    assert store.documents[document.id].processing_status is ProcessingStatus.DELETED
    assert store.pages == {}
    assert store.chunks == {}
    assert await storage.head(document.storage_key) is None
    assert await vectors.count(ALICE, document.id) == 0
    kept = store.citations[citation.id]
    assert kept.document_id == document.id
    assert kept.chunk_id is None
    assert kept.quoted_text == "evidence"


async def test_delete_of_a_foreign_or_unknown_document_is_not_found(
    store: InMemoryStore, service: DocumentService
) -> None:
    document = Factories.document(ALICE)
    store.documents[document.id] = document

    with pytest.raises(DocumentNotFoundError):
        await service.delete(BOB, document.id)
    with pytest.raises(DocumentNotFoundError):
        await service.delete(ALICE, uuid4())
    assert store.documents[document.id] == document


async def test_vector_failure_leaves_the_document_deleting_and_retries(
    store: InMemoryStore,
    storage: FilesystemObjectStorage,
) -> None:
    failing = _FailingVectors()
    service = DocumentService(
        unit_of_work=lambda: InMemoryUnitOfWork(store), storage=storage, vectors=failing
    )
    document = await _seed_ready(store, storage, failing)

    with pytest.raises(VectorStoreUnavailableError):
        await service.delete(ALICE, document.id)

    assert store.documents[document.id].processing_status is ProcessingStatus.DELETING
    assert await storage.head(document.storage_key) is not None
    assert store.pages
    assert await failing.count(ALICE, document.id) == 1

    failing.fail = False
    await service.delete(ALICE, document.id)

    assert store.documents[document.id].is_deleted
    assert await failing.count(ALICE, document.id) == 0
    assert await storage.head(document.storage_key) is None
    assert failing.delete_calls == 2


async def test_object_store_failure_after_vectors_leaves_deletion_resumable(
    store: InMemoryStore, tmp_path: Path, vectors: InMemoryVectorStore
) -> None:
    failing = _FailingStorage(tmp_path / "flaky")
    service = DocumentService(
        unit_of_work=lambda: InMemoryUnitOfWork(store), storage=failing, vectors=vectors
    )
    document = await _seed_ready(store, failing, vectors)

    with pytest.raises(StorageUnavailableError):
        await service.delete(ALICE, document.id)

    assert store.documents[document.id].is_deleting
    assert await vectors.count(ALICE, document.id) == 0, "vectors already gone"
    assert await failing.head(document.storage_key) is not None
    assert store.pages, "relational content waits for the object store"

    failing.fail = False
    await service.delete(ALICE, document.id)

    assert store.documents[document.id].is_deleted
    assert await failing.head(document.storage_key) is None
    assert store.pages == {}
    assert failing.delete_calls == 2


async def test_reprocess_moves_ready_and_failed_documents_to_validating(
    store: InMemoryStore, service: DocumentService
) -> None:
    ready = replace(Factories.document(ALICE), processing_status=ProcessingStatus.READY)
    failed = replace(
        Factories.document(ALICE),
        processing_status=ProcessingStatus.FAILED,
        processing_error="corrupted",
        content_hash="f" * 64,
    )
    store.documents[ready.id] = ready
    store.documents[failed.id] = failed

    restarted = await service.reprocess(ALICE, ready.id)
    retried = await service.reprocess(ALICE, failed.id)

    assert restarted.processing_status is ProcessingStatus.VALIDATING
    assert retried.processing_status is ProcessingStatus.VALIDATING
    assert retried.processing_error is None


async def test_reprocess_of_an_uploaded_document_is_a_no_op(
    store: InMemoryStore, service: DocumentService
) -> None:
    document = Factories.document(ALICE)
    store.documents[document.id] = document

    assert await service.reprocess(ALICE, document.id) == document


async def test_reindex_requires_pages_and_restarts_from_chunking(
    store: InMemoryStore, service: DocumentService
) -> None:
    uploaded = Factories.document(ALICE)
    store.documents[uploaded.id] = uploaded
    with pytest.raises(DocumentCannotReindexError):
        await service.reindex(ALICE, uploaded.id)

    ready = replace(
        Factories.document(ALICE),
        processing_status=ProcessingStatus.READY,
        content_hash="e" * 64,
    )
    page = _page(ready.id)
    store.documents[ready.id] = ready
    store.pages[page.id] = page

    restarted = await service.reindex(ALICE, ready.id)
    assert restarted.processing_status is ProcessingStatus.CHUNKING
    assert await service.reindex(ALICE, ready.id) == restarted, "already restarting"


async def test_reprocess_and_reindex_refuse_a_document_being_deleted(
    store: InMemoryStore, service: DocumentService
) -> None:
    deleting = replace(Factories.document(ALICE), processing_status=ProcessingStatus.DELETING)
    store.documents[deleting.id] = deleting
    page = _page(deleting.id)
    store.pages[page.id] = page

    with pytest.raises(DocumentDeletionInProgressError):
        await service.reprocess(ALICE, deleting.id)
    with pytest.raises(DocumentDeletionInProgressError):
        await service.reindex(ALICE, deleting.id)
    with pytest.raises(DocumentDeletionInProgressError):
        await service.update(ALICE, deleting.id, filename="kept.pdf")


async def test_reprocess_of_a_mid_pipeline_document_is_refused(
    store: InMemoryStore, service: DocumentService
) -> None:
    embedding = replace(Factories.document(ALICE), processing_status=ProcessingStatus.EMBEDDING)
    store.documents[embedding.id] = embedding

    with pytest.raises(InvalidStatusTransitionError):
        await service.reprocess(ALICE, embedding.id)


async def test_tombstones_are_invisible_to_reads(
    store: InMemoryStore, service: DocumentService
) -> None:
    live = Factories.document(ALICE)
    dead = replace(
        Factories.document(ALICE),
        filename="gone.pdf",
        processing_status=ProcessingStatus.DELETED,
        content_hash="d" * 64,
    )
    store.documents[live.id] = live
    store.documents[dead.id] = dead

    assert [d.id for d in await service.list_for_owner(ALICE)] == [live.id]
    assert await service.list_for_owner(ALICE, query="gone") == []
    with pytest.raises(DocumentNotFoundError):
        await service.get(ALICE, dead.id)
    with pytest.raises(DocumentNotFoundError):
        await service.update(ALICE, dead.id, filename="revived.pdf")
    with pytest.raises(DocumentNotFoundError):
        await service.reprocess(ALICE, dead.id)


async def test_rename_cannot_resurrect_a_concurrently_deleted_document(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = Factories.document(ALICE)
    store.documents[document.id] = document
    original_cas = InMemoryDocumentRepository.compare_and_update

    async def racing_cas(
        self: InMemoryDocumentRepository,
        updated: Document,
        *,
        expected_status: ProcessingStatus,
    ) -> bool:
        current = store.documents[document.id]
        store.documents[document.id] = current.transition_to(
            ProcessingStatus.DELETING, now=utc_now()
        )
        return await original_cas(self, updated, expected_status=expected_status)

    monkeypatch.setattr(InMemoryDocumentRepository, "compare_and_update", racing_cas)
    service = DocumentService(unit_of_work=lambda: InMemoryUnitOfWork(store))

    with pytest.raises(DocumentDeletionInProgressError):
        await service.update(ALICE, document.id, filename="revived.pdf")

    assert store.documents[document.id].processing_status is ProcessingStatus.DELETING
    assert store.documents[document.id].filename == document.filename
