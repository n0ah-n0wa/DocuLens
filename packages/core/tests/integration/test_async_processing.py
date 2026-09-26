"""Asynchronous document processing: enqueue, claim, retry, DLQ and idempotency (§48, §66)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003
from uuid import UUID  # noqa: TC003

import pytest

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import DocumentIntakeService, DocumentProcessor, UploadLimits
from doculens.application.jobs import DocumentJobDispatcher, DocumentJobWorker
from doculens.domain.chunking import ChunkingConfig
from doculens.domain.documents import Document, ProcessingStatus
from doculens.domain.errors import DependencyUnavailableError
from doculens.domain.ingestion import (
    CorruptedPdfError,
    ExtractedPage,
    ExtractionResult,
    PdfInfo,
    PdfMetadata,
)
from doculens.domain.storage import PDF_MIME_TYPE, StoredObject
from doculens.infrastructure.queue.memory import InMemoryJobQueue
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.testing.embeddings import FakeEmbeddingProvider
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.pdfs import pdf_with_pages
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.integration

LIMITS = UploadLimits(
    max_file_size_bytes=1024 * 1024, max_pages_per_document=5, max_documents_per_user=10
)
CHUNKER = DocumentChunker(ChunkingConfig(chunk_size=32, chunk_overlap=4, min_chunk_size=4))
INFO = PdfInfo(page_count=1, metadata=PdfMetadata(title="Job", author="Ada"))
PAGES = (ExtractedPage(page_number=1, text="Revenue grew.", width=595.0, height=842.0),)


@dataclass
class FakeExtractor:
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
    """Fails the first ``fail_times`` reads with a transient dependency error."""

    def __init__(self, root: Path, *, fail_times: int = 0) -> None:
        super().__init__(root)
        self.fail_times = fail_times
        self.reads = 0

    async def get(self, key: str) -> StoredObject:
        self.reads += 1
        if self.reads <= self.fail_times:
            message = "object store briefly unavailable"
            raise DependencyUnavailableError(message)
        return await super().get(key)


@dataclass
class World:
    store: InMemoryStore
    storage: FlakyStorage
    extractor: FakeExtractor
    embeddings: FakeEmbeddingProvider
    vectors: InMemoryVectorStore
    queue: InMemoryJobQueue
    intake: DocumentIntakeService
    worker: DocumentJobWorker
    owner_id: UUID
    moments: list[datetime]

    def advance(self, seconds: float) -> None:
        self.moments[0] = self.moments[0] + timedelta(seconds=seconds)

    def document(self, document_id: UUID) -> Document:
        return self.store.documents[document_id]


@pytest.fixture
def world(tmp_path: Path) -> World:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    moments = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return moments[0]

    storage = FlakyStorage(tmp_path / "objects")
    extractor = FakeExtractor()
    embeddings = FakeEmbeddingProvider(dimensions=4)
    vectors = InMemoryVectorStore()
    queue = InMemoryJobQueue(clock=clock)
    dispatcher = DocumentJobDispatcher(queue=queue, clock=clock)
    intake = DocumentIntakeService(
        unit_of_work=lambda: InMemoryUnitOfWork(store),
        storage=storage,
        limits=LIMITS,
        jobs=dispatcher,
        clock=clock,
    )
    processor = DocumentProcessor(
        unit_of_work=lambda: InMemoryUnitOfWork(store),
        storage=storage,
        extractor=extractor,
        chunker=CHUNKER,
        embeddings=embeddings,
        vectors=vectors,
        limits=LIMITS,
        clock=clock,
    )
    worker = DocumentJobWorker(
        queue=queue,
        processor=processor,
        max_attempts=3,
        backoff_base_seconds=1.0,
        backoff_max_seconds=1.0,
        visibility_timeout_seconds=2.0,
        poll_interval_seconds=0.01,
        clock=clock,
    )
    worker.backoff_seconds = lambda _attempt: 1.0  # type: ignore[assignment,method-assign]
    return World(
        store, storage, extractor, embeddings, vectors, queue, intake, worker, owner.id, moments
    )


@pytest.fixture
def sample() -> bytes:
    return pdf_with_pages(["Revenue grew."])


async def test_successful_processing_reaches_ready(world: World, sample: bytes) -> None:
    document = await world.intake.accept(
        world.owner_id,
        filename="report.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )
    assert document.processing_status is ProcessingStatus.UPLOADED

    assert await world.worker.run_once() is True

    stored = world.document(document.id)
    assert stored.processing_status is ProcessingStatus.READY
    assert await world.vectors.count(world.owner_id, document.id) >= 1
    assert await world.queue.list_dead_letters() == []


async def test_transient_failure_retries_then_succeeds(world: World, sample: bytes) -> None:
    world.storage.fail_times = 1
    document = await world.intake.accept(
        world.owner_id,
        filename="report.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )

    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.VALIDATING

    world.advance(1.1)
    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.READY
    assert world.storage.reads >= 2


async def test_permanent_failure_is_acknowledged_without_dead_letter(
    world: World, sample: bytes
) -> None:
    world.extractor.inspect_error = CorruptedPdfError()
    document = await world.intake.accept(
        world.owner_id,
        filename="bad.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )

    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.FAILED
    assert await world.queue.list_dead_letters() == []
    assert await world.queue.claim(visibility_timeout_seconds=1) is None


async def test_duplicate_jobs_are_idempotent(world: World, sample: bytes) -> None:
    document = await world.intake.accept(
        world.owner_id,
        filename="report.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )
    await DocumentJobDispatcher(queue=world.queue, clock=lambda: world.moments[0]).enqueue(
        document.id
    )

    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.READY
    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.READY
    assert await world.queue.list_dead_letters() == []


async def test_worker_interruption_reclaims_the_lease_and_finishes(
    world: World, sample: bytes
) -> None:
    document = await world.intake.accept(
        world.owner_id,
        filename="report.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )
    claim = await world.queue.claim(visibility_timeout_seconds=1.0)
    assert claim is not None
    assert claim.job.document_id == document.id

    world.advance(1.1)
    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.READY


async def test_retry_budget_exhaustion_dead_letters_the_job(world: World, sample: bytes) -> None:
    world.storage.fail_times = 100
    document = await world.intake.accept(
        world.owner_id,
        filename="report.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )

    for _ in range(3):
        assert await world.worker.run_once() is True
        world.advance(1.1)

    dead = await world.queue.list_dead_letters()
    assert len(dead) == 1
    assert dead[0].job.document_id == document.id
    assert world.document(document.id).processing_status is ProcessingStatus.FAILED
    assert world.document(document.id).processing_error is not None


async def test_processing_timeout_retries_before_visibility_expires(
    world: World, sample: bytes
) -> None:
    document = await world.intake.accept(
        world.owner_id,
        filename="report.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )
    real = world.worker._resolve_processor()  # noqa: SLF001

    class SlowProcessor:
        async def process(self, document_id: UUID) -> object:
            del document_id
            await asyncio.sleep(10)
            message = "should have been cancelled"
            raise AssertionError(message)

        async def fail_permanently(self, document_id: UUID, *, reason: str) -> object:
            return await real.fail_permanently(document_id, reason=reason)

    world.worker._processor = SlowProcessor()  # type: ignore[assignment]  # noqa: SLF001
    world.worker._processing_timeout = 0.05  # noqa: SLF001

    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.UPLOADED

    world.worker._processor = real  # noqa: SLF001
    world.advance(1.1)
    assert await world.worker.run_once() is True
    assert world.document(document.id).processing_status is ProcessingStatus.READY


async def test_lease_reclaim_consumes_retry_budget(world: World, sample: bytes) -> None:
    world.storage.fail_times = 100
    document = await world.intake.accept(
        world.owner_id,
        filename="report.pdf",
        declared_mime_type=PDF_MIME_TYPE,
        data=sample,
    )
    # Claim without completing: after visibility expires the reclaim bumps attempt.
    claim = await world.queue.claim(visibility_timeout_seconds=1.0)
    assert claim is not None
    assert claim.job.attempt == 1

    world.advance(1.1)
    reclaimed = await world.queue.claim(visibility_timeout_seconds=1.0)
    assert reclaimed is not None
    assert reclaimed.job.attempt == 2
    assert reclaimed.job.document_id == document.id
