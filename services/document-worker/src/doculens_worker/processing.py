"""Composition of the ingestion pipeline and the job consumer for the worker process.

``process_document`` runs the implemented stages for one id. ``run_worker`` claims jobs from the
configured queue (ADR-006) and drives the same function with retries and dead-lettering (§48).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from doculens.application.ingestion import DocumentProcessor
from doculens.application.jobs import DocumentJobWorker
from doculens.infrastructure.chunking import build_chunker
from doculens.infrastructure.embeddings import build_embedding_provider
from doculens.infrastructure.pdf import build_pdf_extractor, build_upload_limits
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.queue import build_job_queue
from doculens.infrastructure.storage import build_object_storage
from doculens.infrastructure.vectors import build_vector_store

if TYPE_CHECKING:
    from uuid import UUID

    from doculens.application.chunking import DocumentChunker
    from doculens.application.embeddings import EmbeddingProvider
    from doculens.application.ingestion import PdfExtractor, ProcessingReport
    from doculens.application.jobs import JobQueue
    from doculens.application.storage import ObjectStorage
    from doculens.application.unit_of_work import UnitOfWorkFactory
    from doculens.application.vectors import VectorStore
    from doculens.infrastructure.config import CoreSettings


def build_processor(  # noqa: PLR0913 - one optional override per port, for tests
    settings: CoreSettings,
    *,
    unit_of_work: UnitOfWorkFactory,
    storage: ObjectStorage | None = None,
    extractor: PdfExtractor | None = None,
    chunker: DocumentChunker | None = None,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorStore | None = None,
) -> DocumentProcessor:
    return DocumentProcessor(
        unit_of_work=unit_of_work,
        storage=storage if storage is not None else build_object_storage(settings),
        extractor=extractor if extractor is not None else build_pdf_extractor(settings),
        chunker=chunker if chunker is not None else build_chunker(settings),
        embeddings=embeddings if embeddings is not None else build_embedding_provider(settings),
        vectors=vectors if vectors is not None else build_vector_store(settings),
        limits=build_upload_limits(settings),
        index_window=settings.indexing_batch_chunks,
    )


async def process_document(settings: CoreSettings, document_id: UUID) -> ProcessingReport:
    """Run the implemented stages for one document against the configured database and store."""
    database = Database(settings)
    try:
        processor = build_processor(settings, unit_of_work=database.unit_of_work)
        return await processor.process(document_id)
    finally:
        await database.dispose()


def build_job_worker(
    settings: CoreSettings,
    *,
    queue: JobQueue | None = None,
    unit_of_work: UnitOfWorkFactory | None = None,
) -> tuple[DocumentJobWorker, Database | None, JobQueue]:
    """Wire a worker; returns ``(worker, database_or_none, queue)`` so callers can dispose."""
    database: Database | None = None
    if unit_of_work is None:
        database = Database(settings)
        unit_of_work = database.unit_of_work
    resolved_queue = queue if queue is not None else build_job_queue(settings)
    processor = build_processor(settings, unit_of_work=unit_of_work)
    worker = DocumentJobWorker(
        queue=resolved_queue,
        processor=processor,
        max_attempts=settings.queue_max_attempts,
        backoff_base_seconds=settings.queue_backoff_base_seconds,
        backoff_max_seconds=settings.queue_backoff_max_seconds,
        visibility_timeout_seconds=settings.queue_visibility_timeout_seconds,
        processing_timeout_seconds=settings.queue_processing_timeout_seconds,
        poll_interval_seconds=settings.queue_poll_interval_seconds,
    )
    return worker, database, resolved_queue


async def run_worker(
    settings: CoreSettings,
    *,
    stop: asyncio.Event | None = None,
    max_idle_polls: int | None = None,
) -> None:
    """Consume the job queue until ``stop`` is set (or forever)."""
    worker, database, queue = build_job_worker(settings)
    halt = stop if stop is not None else asyncio.Event()
    idle = 0
    try:

        def should_stop() -> bool:
            return halt.is_set() or (max_idle_polls is not None and idle >= max_idle_polls)

        while not should_stop():
            worked = await worker.run_once()
            if worked:
                idle = 0
                continue
            idle += 1
            if max_idle_polls is not None and idle >= max_idle_polls:
                break
            await asyncio.sleep(settings.queue_poll_interval_seconds)
    finally:
        closer = getattr(queue, "close", None)
        if closer is not None:
            await closer()
        if database is not None:
            await database.dispose()
