"""The processor walks the §7.3 states, is idempotent, resumable and safe under duplicate jobs."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import (
    DocumentProcessor,
    ProcessingOutcome,
    ProcessingReport,
    UploadLimits,
)
from doculens.domain.chunking import ChunkingConfig
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.errors import DatabaseUnavailableError
from doculens.domain.ingestion import (
    CorruptedPdfError,
    ExtractedPage,
    ExtractionResult,
    ExtractionTimeoutError,
    PdfInfo,
    PdfMetadata,
)
from doculens.domain.storage import (
    PDF_MIME_TYPE,
    StorageUnavailableError,
    StoredObject,
    content_hash,
    document_object_key,
)
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.testing.embeddings import FakeEmbeddingProvider
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.pdfs import pdf_with_pages
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

LIMITS = UploadLimits(
    max_file_size_bytes=1024 * 1024, max_pages_per_document=5, max_documents_per_user=10
)
CHUNKER = DocumentChunker(ChunkingConfig(chunk_size=32, chunk_overlap=4, min_chunk_size=4))
INFO = PdfInfo(page_count=2, metadata=PdfMetadata(title="Quarterly report", author="Ada"))
PAGES = (
    ExtractedPage(page_number=1, text="Revenue grew.", width=595.0, height=842.0),
    ExtractedPage(page_number=2, text="", width=595.0, height=842.0),
)


@dataclass
class FakeExtractor:
    """Scripted parser: records calls and raises what the test asks for."""

    info: PdfInfo = INFO
    pages: tuple[ExtractedPage, ...] = PAGES
    inspect_error: Exception | None = None
    extract_error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def inspect(self, data: bytes, *, max_pages: int) -> PdfInfo:
        del data, max_pages
        self.calls.append("inspect")
        if self.inspect_error is not None:
            raise self.inspect_error
        return self.info

    async def extract(self, data: bytes, *, max_pages: int) -> ExtractionResult:
        del data, max_pages
        self.calls.append("extract")
        if self.extract_error is not None:
            raise self.extract_error
        return ExtractionResult(info=self.info, pages=self.pages)


class FlakyStorage(FilesystemObjectStorage):
    """A store whose reads fail on demand, as an unreachable S3 would."""

    unavailable = False

    async def get(self, key: str) -> StoredObject:
        if self.unavailable:
            raise StorageUnavailableError
        return await super().get(key)


@dataclass
class World:
    store: InMemoryStore
    storage: FlakyStorage
    extractor: FakeExtractor
    embeddings: FakeEmbeddingProvider
    vectors: InMemoryVectorStore
    processor: DocumentProcessor
    owner_id: UUID
    unit_of_work: Callable[[], InMemoryUnitOfWork]

    async def seed(
        self, data: bytes, *, status: ProcessingStatus = ProcessingStatus.UPLOADED
    ) -> Document:
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

    def pages(self, document_id: UUID) -> list[DocumentPage]:
        pages = [p for p in self.store.pages.values() if p.document_id == document_id]
        return sorted(pages, key=lambda p: p.page_number)

    def chunks(self, document_id: UUID) -> list[DocumentChunk]:
        chunks = [c for c in self.store.chunks.values() if c.document_id == document_id]
        return sorted(chunks, key=lambda c: c.chunk_index)


@pytest.fixture
def world(tmp_path: Path) -> World:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    storage = FlakyStorage(tmp_path / "objects")
    extractor = FakeExtractor()
    embeddings = FakeEmbeddingProvider(dimensions=4)
    vectors = InMemoryVectorStore()

    def unit_of_work() -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(store)

    processor = DocumentProcessor(
        unit_of_work=unit_of_work,
        storage=storage,
        extractor=extractor,
        chunker=CHUNKER,
        embeddings=embeddings,
        vectors=vectors,
        limits=LIMITS,
    )
    return World(store, storage, extractor, embeddings, vectors, processor, owner.id, unit_of_work)


@pytest.fixture
def sample() -> bytes:
    return pdf_with_pages(["Revenue grew.", None])


async def test_an_uploaded_document_is_validated_extracted_and_parked_for_chunking(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample)

    report = await world.processor.process(document.id)

    assert report == ProcessingReport(
        document.id,
        ProcessingOutcome.PROCESSED,
        ProcessingStatus.READY,
        (
            ProcessingStatus.VALIDATING,
            ProcessingStatus.EXTRACTING,
            ProcessingStatus.CHUNKING,
            ProcessingStatus.EMBEDDING,
            ProcessingStatus.INDEXING,
        ),
    )
    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.READY
    assert stored.indexed_at is not None
    assert stored.chunk_count == 1
    chunks = world.chunks(document.id)
    assert [c.text for c in chunks] == ["Revenue grew."]
    assert chunks[0].page_id == world.pages(document.id)[0].id
    assert chunks[0].metadata["page_number"] == 1
    chunking = stored.metadata["chunking"]
    assert isinstance(chunking, dict)
    assert chunking["chunk_count"] == 1
    assert chunking["tokenizer"] == "regex-v1"
    assert stored.page_count == 2
    assert stored.processing_error is None
    assert stored.metadata["pdf"] == {"title": "Quarterly report", "author": "Ada"}
    extraction = stored.metadata["extraction"]
    assert isinstance(extraction, dict)
    assert extraction["page_count"] == 2
    assert extraction["empty_page_count"] == 1
    assert extraction["text_page_count"] == 1
    pages = world.pages(document.id)
    assert [p.page_number for p in pages] == [1, 2]
    assert pages[0].extracted_text == "Revenue grew."
    assert pages[0].metadata["has_text"] is True
    assert pages[1].metadata["has_text"] is False
    assert world.extractor.calls == ["inspect", "extract"]


async def test_processing_twice_is_a_no_op_that_keeps_the_pages(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample)
    await world.processor.process(document.id)
    page_ids = {p.id for p in world.pages(document.id)}

    report = await world.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.NO_OP
    assert report.stages == ()
    assert {p.id for p in world.pages(document.id)} == page_ids
    assert world.extractor.calls == ["inspect", "extract"]


async def test_a_run_resumes_from_the_current_stage_and_replaces_partial_pages(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample, status=ProcessingStatus.EXTRACTING)
    stale = DocumentPage(
        id=uuid4(),
        document_id=document.id,
        page_number=1,
        extracted_text="half written",
        character_count=12,
        metadata={},
    )
    world.store.pages[stale.id] = stale

    report = await world.processor.process(document.id)

    assert report.stages == (
        ProcessingStatus.EXTRACTING,
        ProcessingStatus.CHUNKING,
        ProcessingStatus.EMBEDDING,
        ProcessingStatus.INDEXING,
    )
    assert report.status is ProcessingStatus.READY
    pages = world.pages(document.id)
    assert [p.page_number for p in pages] == [1, 2]
    assert stale.id not in {p.id for p in pages}
    assert world.extractor.calls == ["extract"]


async def test_a_run_resumes_validation_after_a_crash_in_that_stage(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample, status=ProcessingStatus.VALIDATING)

    report = await world.processor.process(document.id)

    assert report.stages == (
        ProcessingStatus.VALIDATING,
        ProcessingStatus.EXTRACTING,
        ProcessingStatus.CHUNKING,
        ProcessingStatus.EMBEDDING,
        ProcessingStatus.INDEXING,
    )
    assert report.status is ProcessingStatus.READY


async def test_a_rejected_file_fails_at_validation_with_a_safe_message(
    world: World, sample: bytes
) -> None:
    world.extractor.inspect_error = CorruptedPdfError(diagnostics="FileDataError: bad xref")
    document = await world.seed(sample)

    report = await world.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.FAILED
    assert report.stages == (ProcessingStatus.VALIDATING,)
    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "The file is not a readable PDF."
    assert "xref" not in (stored.processing_error or "")
    assert world.pages(document.id) == []
    assert world.extractor.calls == ["inspect"]


async def test_too_many_pages_fail_validation(world: World, sample: bytes) -> None:
    world.extractor.info = PdfInfo(page_count=6, metadata=PdfMetadata())
    document = await world.seed(sample)

    await world.processor.process(document.id)

    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "The PDF has more pages than allowed."
    assert stored.page_count is None


async def test_an_extraction_timeout_fails_the_extracting_stage(
    world: World, sample: bytes
) -> None:
    world.extractor.extract_error = ExtractionTimeoutError(diagnostics="no result within 1s")
    document = await world.seed(sample)

    report = await world.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.FAILED
    assert report.stages == (ProcessingStatus.VALIDATING, ProcessingStatus.EXTRACTING)
    assert world.document(document.id).processing_error == "Text extraction took too long."


async def test_unexpected_errors_fail_the_document_without_leaking_details(
    world: World, sample: bytes
) -> None:
    world.extractor.extract_error = RuntimeError("boom: /etc/secret")
    document = await world.seed(sample)

    await world.processor.process(document.id)

    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "Text could not be extracted from the PDF."
    assert "boom" not in (stored.processing_error or "")


async def test_an_unreachable_store_leaves_the_document_retryable(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample)
    world.storage.unavailable = True

    with pytest.raises(StorageUnavailableError):
        await world.processor.process(document.id)

    assert world.document(document.id).processing_status is ProcessingStatus.VALIDATING
    assert world.extractor.calls == []

    world.storage.unavailable = False
    report = await world.processor.process(document.id)
    assert report.status is ProcessingStatus.READY


async def test_a_stored_file_that_no_longer_matches_the_row_is_refused(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample)
    await world.storage.put(
        document.storage_key, sample + b"\n% altered", content_type=PDF_MIME_TYPE
    )

    await world.processor.process(document.id)

    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "The stored file does not match the uploaded document."
    assert world.extractor.calls == []


async def test_unknown_and_deleting_documents_are_skipped(world: World, sample: bytes) -> None:
    missing = await world.processor.process(uuid4())
    deleting = await world.seed(sample, status=ProcessingStatus.DELETING)

    report = await world.processor.process(deleting.id)

    assert missing.outcome is ProcessingOutcome.SKIPPED
    assert report.outcome is ProcessingOutcome.SKIPPED
    assert world.document(deleting.id).processing_status is ProcessingStatus.DELETING


async def test_a_document_moved_by_another_worker_is_left_alone(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample)
    creations = 0
    original_factory = world.unit_of_work

    def racing_unit_of_work() -> InMemoryUnitOfWork:
        nonlocal creations
        creations += 1
        if creations == 2:  # between the read and the first compare-and-set
            current = world.store.documents[document.id]
            world.store.documents[document.id] = replace(
                current, processing_status=ProcessingStatus.VALIDATING
            )
        return original_factory()

    processor = DocumentProcessor(
        unit_of_work=racing_unit_of_work,
        storage=world.storage,
        extractor=world.extractor,
        chunker=CHUNKER,
        embeddings=world.embeddings,
        vectors=world.vectors,
        limits=LIMITS,
    )

    report = await processor.process(document.id)

    assert report.outcome is ProcessingOutcome.CONCURRENT
    assert world.document(document.id).processing_status is ProcessingStatus.VALIDATING
    assert world.extractor.calls == []


async def test_a_failed_document_is_not_retried_until_explicitly_reprocessed(
    world: World, sample: bytes
) -> None:
    world.extractor.inspect_error = CorruptedPdfError()
    document = await world.seed(sample)
    await world.processor.process(document.id)

    untouched = await world.processor.process(document.id)
    assert untouched.outcome is ProcessingOutcome.NO_OP
    assert untouched.status is ProcessingStatus.FAILED

    # Reprocessing (§29) moves FAILED back to VALIDATING; the next run picks it up.
    world.extractor.inspect_error = None
    failed = world.document(document.id)
    world.store.documents[document.id] = failed.transition_to(
        ProcessingStatus.VALIDATING, now=failed.updated_at
    )
    report = await world.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.PROCESSED
    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.READY
    assert stored.processing_error is None


async def test_a_run_resuming_at_chunking_replaces_chunks_without_duplicates(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample)
    await world.processor.process(document.id)
    first_ids = [c.id for c in world.chunks(document.id)]
    current = world.document(document.id)
    world.store.documents[document.id] = replace(
        current, processing_status=ProcessingStatus.CHUNKING
    )

    report = await world.processor.process(document.id)

    assert report.stages == (
        ProcessingStatus.CHUNKING,
        ProcessingStatus.EMBEDDING,
        ProcessingStatus.INDEXING,
    )
    assert report.status is ProcessingStatus.READY
    assert [c.id for c in world.chunks(document.id)] == first_ids


async def test_a_document_without_text_still_reaches_embedding_with_zero_chunks(
    world: World, sample: bytes
) -> None:
    world.extractor.pages = (ExtractedPage(page_number=1, text="", width=1.0, height=1.0),)
    world.extractor.info = PdfInfo(page_count=1, metadata=PdfMetadata())
    document = await world.seed(sample)

    report = await world.processor.process(document.id)

    assert report.status is ProcessingStatus.READY
    assert world.document(document.id).chunk_count == 0
    assert world.chunks(document.id) == []
    assert await world.vectors.count(world.owner_id, document.id) == 0


async def test_a_database_outage_leaves_the_document_retryable(world: World, sample: bytes) -> None:
    document = await world.seed(sample)
    creations = 0
    original_factory = world.unit_of_work

    class OutageUnitOfWork(InMemoryUnitOfWork):
        async def __aenter__(self) -> "OutageUnitOfWork":
            raise DatabaseUnavailableError

    def flaky_unit_of_work() -> InMemoryUnitOfWork:
        nonlocal creations
        creations += 1
        if creations == 4:  # the unit of work that would persist the extraction
            return OutageUnitOfWork(world.store)
        return original_factory()

    processor = DocumentProcessor(
        unit_of_work=flaky_unit_of_work,
        storage=world.storage,
        extractor=world.extractor,
        chunker=CHUNKER,
        embeddings=world.embeddings,
        vectors=world.vectors,
        limits=LIMITS,
    )

    with pytest.raises(DatabaseUnavailableError):
        await processor.process(document.id)

    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.EXTRACTING
    assert stored.processing_error is None


async def test_a_missing_stored_object_fails_with_a_specific_message(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample)
    await world.storage.delete(document.storage_key)

    await world.processor.process(document.id)

    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "The uploaded file is no longer available."


async def test_re_extraction_discards_metadata_derived_from_the_previous_pages(
    world: World, sample: bytes
) -> None:
    document = await world.seed(sample, status=ProcessingStatus.EXTRACTING)
    world.store.documents[document.id] = replace(
        document, metadata={"chunking": {"stale": True}, "pdf": {"title": "old"}}
    )

    await world.processor.process(document.id)

    stored = world.document(document.id)
    chunking = stored.metadata["chunking"]
    assert isinstance(chunking, dict)
    assert "stale" not in chunking
    assert stored.metadata["pdf"] == {"title": "Quarterly report", "author": "Ada"}
