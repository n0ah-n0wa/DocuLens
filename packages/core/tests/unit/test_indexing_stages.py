"""EMBEDDING and INDEXING: idempotent, retryable, deterministic ids, no duplicates, owner-safe."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import DocumentProcessor, ProcessingOutcome, UploadLimits
from doculens.domain.chunking import ChunkingConfig, chunk_id
from doculens.domain.documents import Document, ProcessingStatus
from doculens.domain.embeddings import (
    EmbeddingProviderUnavailableError,
    EmbeddingRequestRejectedError,
)
from doculens.domain.ingestion import ExtractedPage, ExtractionResult, PdfInfo, PdfMetadata
from doculens.domain.storage import PDF_MIME_TYPE, content_hash, document_object_key
from doculens.domain.vectors import SearchFilter, VectorRecord, VectorStoreUnavailableError
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.testing.embeddings import FakeEmbeddingProvider, hashed_vector
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.pdfs import pdf_with_pages
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

LIMITS = UploadLimits(
    max_file_size_bytes=1 << 20, max_pages_per_document=5, max_documents_per_user=10
)
CHUNKER = DocumentChunker(ChunkingConfig(chunk_size=8, chunk_overlap=0, min_chunk_size=2))
PAGES = (
    ExtractedPage(
        page_number=1, text="alpha beta gamma delta.\n\nepsilon zeta eta theta.", width=1, height=1
    ),
    ExtractedPage(page_number=2, text="iota kappa lambda mu.", width=1, height=1),
)


class ScriptedExtractor:
    async def inspect(self, data: bytes, *, max_pages: int) -> PdfInfo:
        del data, max_pages
        return PdfInfo(page_count=2, metadata=PdfMetadata(title="T"))

    async def extract(self, data: bytes, *, max_pages: int) -> ExtractionResult:
        del data, max_pages
        return ExtractionResult(
            info=PdfInfo(page_count=2, metadata=PdfMetadata(title="T")), pages=PAGES
        )


class FlakyVectorStore(InMemoryVectorStore):
    """Fails an ``upsert`` on demand, after writing half of it, like a mid-batch outage."""

    failures = 0

    async def upsert(self, owner_id: UUID, records: Sequence[VectorRecord]) -> None:
        if self.failures > 0:
            self.failures -= 1
            await super().upsert(owner_id, records[: max(1, len(records) // 2)])  # partial write
            raise VectorStoreUnavailableError
        await super().upsert(owner_id, records)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.store = InMemoryStore()
        owner = Factories.user()
        self.store.users[owner.id] = owner
        self.owner_id = owner.id
        self.storage = FilesystemObjectStorage(tmp_path / "objects")
        self.embeddings = FakeEmbeddingProvider(dimensions=6)
        self.vectors = FlakyVectorStore()
        self.processor = DocumentProcessor(
            unit_of_work=lambda: InMemoryUnitOfWork(self.store),
            storage=self.storage,
            extractor=ScriptedExtractor(),
            chunker=CHUNKER,
            embeddings=self.embeddings,
            vectors=self.vectors,
            limits=LIMITS,
        )

    async def seed(self, status: ProcessingStatus = ProcessingStatus.UPLOADED) -> Document:
        data = pdf_with_pages(["alpha", "iota"])
        document = replace(
            Factories.document(self.owner_id),
            content_hash=content_hash(data),
            file_size=len(data),
            processing_status=status,
        )
        document = replace(document, storage_key=document_object_key(self.owner_id, document.id))
        await self.storage.put(document.storage_key, data, content_type=PDF_MIME_TYPE)
        self.store.documents[document.id] = document
        return document

    def document(self, document_id: UUID) -> Document:
        return self.store.documents[document_id]

    def set_status(self, document_id: UUID, status: ProcessingStatus) -> None:
        self.store.documents[document_id] = replace(
            self.store.documents[document_id], processing_status=status
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


async def test_chunks_are_embedded_indexed_under_their_ids_and_the_document_becomes_ready(
    world: World,
) -> None:
    document = await world.seed()

    report = await world.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.PROCESSED
    assert report.status is ProcessingStatus.READY
    stored = world.document(document.id)
    chunks = sorted(
        (c for c in world.store.chunks.values() if c.document_id == document.id),
        key=lambda c: c.chunk_index,
    )
    assert len(chunks) == 3
    assert stored.chunk_count == 3
    assert stored.indexed_at is not None
    # Deterministic vector ids: the chunk ids, recorded on the chunk rows.
    assert [c.vector_id for c in chunks] == [str(chunk_id(document.id, i)) for i in range(3)]
    assert set(world.vectors.records) == {c.vector_id for c in chunks}
    # Ownership-safe metadata and provenance on every vector.
    for chunk in chunks:
        vector = world.vectors.records[str(chunk.id)]
        assert vector.metadata.user_id == world.owner_id
        assert vector.metadata.document_id == document.id
        assert vector.metadata.page_number == chunk.metadata["page_number"]
        assert vector.metadata.embedding_model == "fake-embedding-v1"
        assert vector.metadata.embedding_provider == "fake"
        assert vector.text == chunk.text
        assert vector.vector == hashed_vector(chunk.text, 6)
    indexing = stored.metadata["indexing"]
    assert isinstance(indexing, dict)
    assert indexing["vector_count"] == 3
    assert indexing["embedding_requests"] == 1
    assert indexing["embedding_model"] == "fake-embedding-v1"
    assert indexing["stale_vectors_removed"] == 0
    assert world.embeddings.document_calls == [[c.text for c in chunks]]


async def test_a_second_run_is_a_no_op_and_writes_nothing(world: World) -> None:
    document = await world.seed()
    await world.processor.process(document.id)

    report = await world.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.NO_OP
    assert len(world.embeddings.document_calls) == 1
    assert await world.vectors.count(world.owner_id, document.id) == 3


async def test_a_transient_provider_failure_keeps_the_document_retryable(world: World) -> None:
    document = await world.seed()
    world.embeddings.failures.append(EmbeddingProviderUnavailableError())

    with pytest.raises(EmbeddingProviderUnavailableError):
        await world.processor.process(document.id)

    assert world.document(document.id).processing_status is ProcessingStatus.EMBEDDING
    assert await world.vectors.count(world.owner_id, document.id) == 0  # nothing partial

    report = await world.processor.process(document.id)

    assert report.stages == (ProcessingStatus.EMBEDDING, ProcessingStatus.INDEXING)
    assert report.status is ProcessingStatus.READY
    assert await world.vectors.count(world.owner_id, document.id) == 3


async def test_a_permanent_provider_rejection_fails_the_document_safely(world: World) -> None:
    document = await world.seed()
    world.embeddings.failures.append(
        EmbeddingRequestRejectedError(status_code=401, diagnostics="http 401: bad key")
    )

    report = await world.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.FAILED
    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "Embeddings could not be generated for the document."
    assert "bad key" not in (stored.processing_error or "")
    assert await world.vectors.count(world.owner_id, document.id) == 0


async def test_a_partial_index_write_is_repaired_by_the_next_run_without_duplicates(
    world: World,
) -> None:
    document = await world.seed()
    world.vectors.failures = 1

    with pytest.raises(VectorStoreUnavailableError):
        await world.processor.process(document.id)

    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.EMBEDDING
    assert stored.chunk_count == 3
    assert all(c.vector_id is None for c in world.store.chunks.values())
    written = await world.vectors.count(world.owner_id, document.id)
    assert 0 < written < 3  # partial

    report = await world.processor.process(document.id)

    assert report.stages == (ProcessingStatus.EMBEDDING, ProcessingStatus.INDEXING)
    assert report.status is ProcessingStatus.READY
    assert await world.vectors.count(world.owner_id, document.id) == 3
    assert len(world.vectors.records) == 3
    # Only the chunks whose vectors were missing are embedded again.
    assert len(world.embeddings.document_calls) == 2
    assert len(world.embeddings.document_calls[1]) == 3 - written
    indexing = world.document(document.id).metadata["indexing"]
    assert isinstance(indexing, dict)
    assert indexing["embedded_now"] == 3 - written


async def test_resuming_at_indexing_reuses_current_vectors_and_re_embeds_changed_ones(
    world: World,
) -> None:
    document = await world.seed()
    await world.processor.process(document.id)
    chunks = sorted(
        (c for c in world.store.chunks.values() if c.document_id == document.id),
        key=lambda c: c.chunk_index,
    )
    # Simulate re-extraction that changed one chunk's text (same id, new content hash).
    changed = replace(
        chunks[1],
        text="completely different words",
        metadata={**chunks[1].metadata, "content_hash": "new"},
    )
    world.store.chunks[changed.id] = changed
    world.set_status(document.id, ProcessingStatus.INDEXING)

    report = await world.processor.process(document.id)

    assert report.stages == (ProcessingStatus.INDEXING,)
    assert report.status is ProcessingStatus.READY
    assert world.embeddings.document_calls[-1] == ["completely different words"]
    assert world.vectors.records[str(changed.id)].vector == hashed_vector(changed.text, 6)
    assert world.vectors.records[str(changed.id)].metadata.content_hash == "new"
    assert await world.vectors.count(world.owner_id, document.id) == 3


async def test_a_changed_embedding_model_re_embeds_every_chunk(world: World) -> None:
    document = await world.seed()
    await world.processor.process(document.id)

    world.embeddings.model_name = "fake-embedding-v2"
    world.set_status(document.id, ProcessingStatus.EMBEDDING)
    report = await world.processor.process(document.id)

    assert report.status is ProcessingStatus.READY
    assert len(world.embeddings.document_calls[-1]) == 3
    assert all(
        r.metadata.embedding_model == "fake-embedding-v2" for r in world.vectors.records.values()
    )


async def test_large_documents_are_embedded_in_bounded_windows(world: World) -> None:
    processor = DocumentProcessor(
        unit_of_work=lambda: InMemoryUnitOfWork(world.store),
        storage=world.storage,
        extractor=ScriptedExtractor(),
        chunker=CHUNKER,
        embeddings=world.embeddings,
        vectors=world.vectors,
        limits=LIMITS,
        index_window=2,
    )
    document = await world.seed()

    report = await processor.process(document.id)

    assert report.status is ProcessingStatus.READY
    assert [len(call) for call in world.embeddings.document_calls] == [2, 1]
    assert await world.vectors.count(world.owner_id, document.id) == 3


async def test_reindexing_after_rechunking_removes_stale_vectors_and_keeps_stable_ids(
    world: World,
) -> None:
    document = await world.seed()
    await world.processor.process(document.id)
    original_ids = sorted(world.vectors.records)

    # Re-chunk with a larger budget: fewer chunks, same id scheme; run from CHUNKING again.
    coarse = DocumentProcessor(
        unit_of_work=lambda: InMemoryUnitOfWork(world.store),
        storage=world.storage,
        extractor=ScriptedExtractor(),
        chunker=DocumentChunker(ChunkingConfig(chunk_size=64, chunk_overlap=0, min_chunk_size=1)),
        embeddings=world.embeddings,
        vectors=world.vectors,
        limits=LIMITS,
    )
    world.set_status(document.id, ProcessingStatus.CHUNKING)

    report = await coarse.process(document.id)

    assert report.status is ProcessingStatus.READY
    remaining = sorted(world.vectors.records)
    assert len(remaining) == 2  # one chunk per page now
    assert set(remaining) <= set(original_ids)
    indexing = world.document(document.id).metadata["indexing"]
    assert isinstance(indexing, dict)
    assert indexing["stale_vectors_removed"] == 1


async def test_vectors_of_other_users_are_untouched_by_a_reindex(world: World) -> None:
    other = Factories.user("other@example.com")
    world.store.users[other.id] = other
    document = await world.seed()
    await world.processor.process(document.id)
    theirs = replace(
        world.vectors.records[str(chunk_id(document.id, 0))],
    )
    # Plant a vector for the other user under a different document id in the same store.
    foreign_document = Factories.document(other.id)
    foreign = replace(
        theirs,
        id=str(chunk_id(foreign_document.id, 0)),
        metadata=replace(
            theirs.metadata,
            user_id=other.id,
            document_id=foreign_document.id,
            chunk_id=chunk_id(foreign_document.id, 0),
        ),
    )
    await world.vectors.upsert(other.id, [foreign])

    world.set_status(document.id, ProcessingStatus.INDEXING)
    await world.processor.process(document.id)

    assert await world.vectors.count(other.id) == 1
    assert (
        await world.vectors.search(
            foreign.vector,
            scope=SearchFilter(owner_id=world.owner_id, document_ids=(foreign_document.id,)),
            limit=5,
        )
        == []
    )


async def test_an_empty_document_becomes_ready_without_vectors(world: World) -> None:
    document = await world.seed(status=ProcessingStatus.EMBEDDING)  # no chunks were stored

    report = await world.processor.process(document.id)

    assert report.status is ProcessingStatus.READY
    assert world.document(document.id).chunk_count == 0
    assert world.embeddings.document_calls == []
    assert await world.vectors.count(world.owner_id, document.id) == 0
