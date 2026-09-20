"""Composition of the ingestion pipeline for the worker process.

The queue consumer (§48, ADR-006) will call :func:`process_document` once per job; until it
exists the entrypoint exposes the same function as a ``process`` sub-command so a document can be
pushed through the implemented stages locally or by an operator.
"""

from uuid import UUID

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import DocumentProcessor, PdfExtractor, ProcessingReport
from doculens.application.storage import ObjectStorage
from doculens.application.unit_of_work import UnitOfWorkFactory
from doculens.infrastructure.chunking import build_chunker
from doculens.infrastructure.config import CoreSettings
from doculens.infrastructure.pdf import build_pdf_extractor, build_upload_limits
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.storage import build_object_storage


def build_processor(
    settings: CoreSettings,
    *,
    unit_of_work: UnitOfWorkFactory,
    storage: ObjectStorage | None = None,
    extractor: PdfExtractor | None = None,
    chunker: DocumentChunker | None = None,
) -> DocumentProcessor:
    return DocumentProcessor(
        unit_of_work=unit_of_work,
        storage=storage if storage is not None else build_object_storage(settings),
        extractor=extractor if extractor is not None else build_pdf_extractor(settings),
        chunker=chunker if chunker is not None else build_chunker(settings),
        limits=build_upload_limits(settings),
    )


async def process_document(settings: CoreSettings, document_id: UUID) -> ProcessingReport:
    """Run the implemented stages for one document against the configured database and store."""
    database = Database(settings)
    try:
        processor = build_processor(settings, unit_of_work=database.unit_of_work)
        return await processor.process(document_id)
    finally:
        await database.dispose()
