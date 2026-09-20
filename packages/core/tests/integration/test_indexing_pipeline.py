"""The complete pipeline end to end: PostgreSQL, PyMuPDF, the fake embedding provider, ChromaDB.

Document → pages → chunks → embeddings → ChromaDB → READY, with idempotent re-runs, resume
after a crash, re-indexing without duplicates, retryable failures and cross-user isolation in
the shared index.
"""

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import (
    DocumentIntakeService,
    DocumentProcessor,
    ProcessingOutcome,
    UploadLimits,
)
from doculens.domain.chunking import ChunkingConfig, chunk_id
from doculens.domain.documents import Document, DocumentChunk, ProcessingStatus
from doculens.domain.embeddings import EmbeddingProviderUnavailableError
from doculens.domain.vectors import SearchFilter
from doculens.infrastructure.pdf import ExtractionLimits, PyMuPdfExtractor
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.infrastructure.vectors import ChromaVectorStore
from doculens.testing.chroma import VectorStoreUnavailableForTestsError, provisioned_chroma_url
from doculens.testing.embeddings import FakeEmbeddingProvider, hashed_vector
from doculens.testing.factories import Factories
from doculens.testing.pdfs import pdf_with_pages

pytestmark = pytest.mark.integration

LIMITS = UploadLimits(
    max_file_size_bytes=256 * 1024, max_pages_per_document=10, max_documents_per_user=20
)
PDF_MIME = "application/pdf"
DIMENSIONS = 16


@pytest.fixture(scope="session")
def chroma_url() -> Iterator[str]:
    try:
        with provisioned_chroma_url() as url:
            yield url
    except VectorStoreUnavailableForTestsError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(str(exc))


@pytest.fixture
async def vectors(chroma_url: str) -> AsyncIterator[ChromaVectorStore]:
    store = ChromaVectorStore.from_url(chroma_url, collection=f"doculens-e2e-{uuid4().hex[:12]}")
    await store.ensure_collection()
    try:
        yield store
    finally:
        await store.drop_collection()


class Pipeline:
    def __init__(self, database: Database, root: Path, vectors: ChromaVectorStore) -> None:
        self.database = database
        self.storage = FilesystemObjectStorage(root)
        self.embeddings = FakeEmbeddingProvider(dimensions=DIMENSIONS)
        self.vectors = vectors
        self.intake = DocumentIntakeService(
            unit_of_work=database.unit_of_work, storage=self.storage, limits=LIMITS
        )
        self.processor = self.processor_with(
            DocumentChunker(ChunkingConfig(chunk_size=24, chunk_overlap=4, min_chunk_size=4))
        )

    def processor_with(self, chunker: DocumentChunker) -> DocumentProcessor:
        return DocumentProcessor(
            unit_of_work=self.database.unit_of_work,
            storage=self.storage,
            extractor=PyMuPdfExtractor(
                ExtractionLimits(
                    timeout_seconds=60.0,
                    memory_limit_bytes=None,
                    max_characters_per_page=100_000,
                    max_total_characters=1_000_000,
                )
            ),
            chunker=chunker,
            embeddings=self.embeddings,
            vectors=self.vectors,
            limits=LIMITS,
        )

    async def user(self, email: str) -> UUID:
        user = Factories.user(email)
        async with self.database.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.commit()
        return user.id

    async def upload(self, owner: UUID, data: bytes, filename: str = "report.pdf") -> Document:
        return await self.intake.accept(
            owner, filename=filename, declared_mime_type=PDF_MIME, data=data
        )

    async def document(self, owner: UUID, document_id: UUID) -> Document:
        async with self.database.unit_of_work() as uow:
            document = await uow.documents.get(owner, document_id)
        assert document is not None
        return document

    async def chunks(self, owner: UUID, document_id: UUID) -> list[DocumentChunk]:
        async with self.database.unit_of_work() as uow:
            return await uow.document_content.list_chunks(owner, document_id)

    async def set_status(self, owner: UUID, document_id: UUID, status: ProcessingStatus) -> None:
        async with self.database.unit_of_work() as uow:
            current = await uow.documents.get(owner, document_id)
            assert current is not None
            assert await uow.documents.compare_and_update(
                replace(current, processing_status=status),
                expected_status=current.processing_status,
            )
            await uow.commit()


@pytest.fixture
def pipeline(database: Database, tmp_path: Path, vectors: ChromaVectorStore) -> Pipeline:
    return Pipeline(database, tmp_path / "objects", vectors)


async def test_a_document_travels_from_upload_to_ready_with_its_vectors_in_chroma(
    pipeline: Pipeline,
) -> None:
    owner = await pipeline.user("alice@example.com")
    data = pdf_with_pages(
        [
            "Revenue grew twelve percent in the third quarter. Costs were flat.",
            "Outlook is stable.",
        ],
        metadata={"title": "Quarterly report"},
    )
    document = await pipeline.upload(owner, data)

    report = await pipeline.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.PROCESSED
    assert report.stages == (
        ProcessingStatus.VALIDATING,
        ProcessingStatus.EXTRACTING,
        ProcessingStatus.CHUNKING,
        ProcessingStatus.EMBEDDING,
        ProcessingStatus.INDEXING,
    )
    stored = await pipeline.document(owner, document.id)
    assert stored.processing_status is ProcessingStatus.READY
    assert stored.indexed_at is not None
    chunks = await pipeline.chunks(owner, document.id)
    assert stored.chunk_count == len(chunks) >= 2
    assert [c.vector_id for c in chunks] == [str(c.id) for c in chunks]
    assert await pipeline.vectors.count(owner, document.id) == len(chunks)

    # The vectors are exactly the chunks' embeddings, with ownership and source metadata.
    first = chunks[0]
    hits = await pipeline.vectors.search(
        hashed_vector(first.text, DIMENSIONS), scope=SearchFilter(owner_id=owner), limit=1
    )
    assert hits[0].id == str(first.id)
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
    assert hits[0].metadata.user_id == owner
    assert hits[0].metadata.document_id == document.id
    assert hits[0].metadata.page_number == first.metadata["page_number"]
    assert hits[0].metadata.chunk_index == 0
    assert hits[0].metadata.embedding_model == "fake-embedding-v1"
    assert hits[0].text == first.text
    indexing = stored.metadata["indexing"]
    assert isinstance(indexing, dict)
    assert indexing["vector_count"] == len(chunks)
    assert indexing["vector_store"] == pipeline.vectors.collection


async def test_processing_is_idempotent_and_resuming_at_indexing_never_duplicates(
    pipeline: Pipeline,
) -> None:
    owner = await pipeline.user("alice@example.com")
    document = await pipeline.upload(owner, pdf_with_pages(["one two three", "four five six"]))
    await pipeline.processor.process(document.id)
    vector_count = await pipeline.vectors.count(owner, document.id)
    calls_before = len(pipeline.embeddings.document_calls)

    again = await pipeline.processor.process(document.id)
    assert again.outcome is ProcessingOutcome.NO_OP
    assert len(pipeline.embeddings.document_calls) == calls_before

    # A crash between embedding and indexing leaves INDEXING; the next run recomputes and
    # overwrites the same ids.
    await pipeline.set_status(owner, document.id, ProcessingStatus.INDEXING)
    resumed = await pipeline.processor.process(document.id)

    assert resumed.stages == (ProcessingStatus.INDEXING,)
    assert resumed.status is ProcessingStatus.READY
    assert await pipeline.vectors.count(owner, document.id) == vector_count
    info = await pipeline.vectors.ensure_collection()
    assert info.count == vector_count


async def test_reindexing_with_a_new_chunking_removes_stale_vectors(pipeline: Pipeline) -> None:
    owner = await pipeline.user("alice@example.com")
    page = " ".join(f"Sentence {index} states a fact." for index in range(30))
    document = await pipeline.upload(owner, pdf_with_pages([page, page]))
    await pipeline.processor.process(document.id)
    fine_count = await pipeline.vectors.count(owner, document.id)
    assert fine_count > 2

    coarse = pipeline.processor_with(
        DocumentChunker(ChunkingConfig(chunk_size=512, chunk_overlap=0, min_chunk_size=1))
    )
    await pipeline.set_status(owner, document.id, ProcessingStatus.CHUNKING)
    report = await coarse.process(document.id)

    assert report.status is ProcessingStatus.READY
    chunks = await pipeline.chunks(owner, document.id)
    assert len(chunks) == 2
    assert await pipeline.vectors.count(owner, document.id) == 2
    assert {c.vector_id for c in chunks} == {str(chunk_id(document.id, i)) for i in range(2)}
    indexing = (await pipeline.document(owner, document.id)).metadata["indexing"]
    assert isinstance(indexing, dict)
    assert indexing["stale_vectors_removed"] == fine_count - 2


async def test_a_transient_provider_outage_leaves_the_document_retryable_with_no_partial_index(
    pipeline: Pipeline,
) -> None:
    owner = await pipeline.user("alice@example.com")
    document = await pipeline.upload(owner, pdf_with_pages(["retry me please"]))
    pipeline.embeddings.failures.append(EmbeddingProviderUnavailableError())

    with pytest.raises(EmbeddingProviderUnavailableError):
        await pipeline.processor.process(document.id)

    assert (
        await pipeline.document(owner, document.id)
    ).processing_status is ProcessingStatus.EMBEDDING
    assert await pipeline.vectors.count(owner, document.id) == 0

    report = await pipeline.processor.process(document.id)

    assert report.status is ProcessingStatus.READY
    assert await pipeline.vectors.count(owner, document.id) >= 1


async def test_two_users_share_the_index_but_never_each_others_vectors(
    pipeline: Pipeline,
) -> None:
    alice = await pipeline.user("alice@example.com")
    bob = await pipeline.user("bob@example.com")
    shared_text = "identical content uploaded by two different people"
    alice_document = await pipeline.upload(alice, pdf_with_pages([shared_text]))
    bob_document = await pipeline.upload(bob, pdf_with_pages([shared_text]), filename="b.pdf")
    await pipeline.processor.process(alice_document.id)
    await pipeline.processor.process(bob_document.id)

    query = hashed_vector((await pipeline.chunks(alice, alice_document.id))[0].text, DIMENSIONS)
    alice_hits = await pipeline.vectors.search(query, scope=SearchFilter(owner_id=alice), limit=10)
    bob_hits = await pipeline.vectors.search(query, scope=SearchFilter(owner_id=bob), limit=10)

    assert {hit.document_id for hit in alice_hits} == {alice_document.id}
    assert {hit.document_id for hit in bob_hits} == {bob_document.id}
    assert all(hit.metadata.user_id == alice for hit in alice_hits)
    assert await pipeline.vectors.delete_document(bob, alice_document.id) == 0
    assert await pipeline.vectors.count(alice, alice_document.id) == len(alice_hits)
