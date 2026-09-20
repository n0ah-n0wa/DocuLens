"""The ingestion pipeline end to end: real PostgreSQL, real PyMuPDF, a filesystem object store.

Covers the §64 adversarial inputs for ingestion: valid, corrupted, empty (no pages), pages
without text, oversized, too many pages, duplicate content, plus idempotent re-runs.
"""

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from doculens.application.chunking import DocumentChunker
from doculens.application.ingestion import (
    DocumentIntakeService,
    DocumentProcessor,
    ProcessingOutcome,
    UploadLimits,
)
from doculens.domain.chunking import ChunkingConfig
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.ingestion import (
    DocumentLimitReachedError,
    DuplicateDocumentError,
    FileTooLargeError,
)
from doculens.domain.storage import content_hash
from doculens.infrastructure.pdf import ExtractionLimits, PyMuPdfExtractor
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.testing.factories import Factories
from doculens.testing.pdfs import ZERO_PAGE_PDF, corrupted_pdf, padded_pdf, pdf_with_pages

pytestmark = pytest.mark.integration

LIMITS = UploadLimits(
    max_file_size_bytes=256 * 1024, max_pages_per_document=5, max_documents_per_user=10
)
PDF_MIME = "application/pdf"


class Pipeline:
    def __init__(self, database: Database, root: Path, owner_id: UUID) -> None:
        self.database = database
        self.owner_id = owner_id
        self.storage = FilesystemObjectStorage(root)
        self.intake = DocumentIntakeService(
            unit_of_work=database.unit_of_work, storage=self.storage, limits=LIMITS
        )
        self.processor = DocumentProcessor(
            unit_of_work=database.unit_of_work,
            storage=self.storage,
            extractor=PyMuPdfExtractor(
                ExtractionLimits(
                    timeout_seconds=60.0,
                    memory_limit_bytes=None,
                    max_characters_per_page=100_000,
                    max_total_characters=1_000_000,
                )
            ),
            chunker=DocumentChunker(
                ChunkingConfig(chunk_size=32, chunk_overlap=4, min_chunk_size=4)
            ),
            limits=LIMITS,
        )

    async def upload(self, data: bytes, filename: str = "report.pdf") -> Document:
        return await self.intake.accept(
            self.owner_id, filename=filename, declared_mime_type=PDF_MIME, data=data
        )

    async def document(self, document_id: UUID) -> Document:
        async with self.database.unit_of_work() as uow:
            document = await uow.documents.get(self.owner_id, document_id)
        assert document is not None
        return document

    async def pages(self, document_id: UUID) -> list[DocumentPage]:
        async with self.database.unit_of_work() as uow:
            return await uow.document_content.list_pages(self.owner_id, document_id)

    async def chunks(self, document_id: UUID) -> list[DocumentChunk]:
        async with self.database.unit_of_work() as uow:
            return await uow.document_content.list_chunks(self.owner_id, document_id)

    async def all_documents(self) -> list[Document]:
        async with self.database.unit_of_work() as uow:
            return await uow.documents.list_for_owner(self.owner_id)

    def stored_objects(self) -> list[Path]:
        root = self.storage.root / "objects"
        return sorted(root.rglob("original.pdf")) if root.exists() else []


@pytest.fixture
async def pipeline(database: Database, tmp_path: Path) -> Pipeline:
    owner = Factories.user()
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.commit()
    return Pipeline(database, tmp_path / "objects", owner.id)


async def test_a_valid_pdf_is_extracted_page_by_page_with_its_metadata(pipeline: Pipeline) -> None:
    data = pdf_with_pages(
        ["Revenue grew 12% in Q3.", "Costs were flat."],
        metadata={"title": "Quarterly report", "author": "Ada"},
    )
    document = await pipeline.upload(data)

    report = await pipeline.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.PROCESSED
    assert report.stages == (
        ProcessingStatus.VALIDATING,
        ProcessingStatus.EXTRACTING,
        ProcessingStatus.CHUNKING,
    )
    stored = await pipeline.document(document.id)
    assert stored.processing_status is ProcessingStatus.EMBEDDING
    chunks = await pipeline.chunks(document.id)
    assert stored.chunk_count == len(chunks) == 2
    assert [c.metadata["page_number"] for c in chunks] == [1, 2]
    assert [c.chunk_index for c in chunks] == [0, 1]
    assert stored.page_count == 2
    assert stored.processing_error is None
    assert stored.metadata["pdf"] == {
        "title": "Quarterly report",
        "author": "Ada",
        "pdf_version": "PDF 1.7",
    }
    extraction = stored.metadata["extraction"]
    assert isinstance(extraction, dict)
    assert extraction["page_count"] == 2
    assert extraction["empty_page_count"] == 0
    pages = await pipeline.pages(document.id)
    assert [p.page_number for p in pages] == [1, 2]
    assert "Revenue grew 12% in Q3." in pages[0].extracted_text
    assert "Costs were flat." in pages[1].extracted_text
    assert all(p.metadata["has_text"] is True for p in pages)
    assert pages[0].character_count == len(pages[0].extracted_text)


async def test_a_corrupted_pdf_fails_safely_at_validation(pipeline: Pipeline) -> None:
    document = await pipeline.upload(corrupted_pdf())

    report = await pipeline.processor.process(document.id)

    assert report.outcome is ProcessingOutcome.FAILED
    stored = await pipeline.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "The file is not a readable PDF."
    assert await pipeline.pages(document.id) == []


async def test_a_pdf_without_pages_is_rejected(pipeline: Pipeline) -> None:
    document = await pipeline.upload(ZERO_PAGE_PDF)

    await pipeline.processor.process(document.id)

    stored = await pipeline.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "The PDF contains no pages."


async def test_pages_without_text_are_recorded_not_dropped(pipeline: Pipeline) -> None:
    document = await pipeline.upload(pdf_with_pages(["Only this page has text.", None, None]))

    await pipeline.processor.process(document.id)

    stored = await pipeline.document(document.id)
    assert stored.processing_status is ProcessingStatus.EMBEDDING
    assert stored.page_count == 3
    assert stored.chunk_count == 1
    extraction = stored.metadata["extraction"]
    assert isinstance(extraction, dict)
    assert extraction["empty_page_count"] == 2
    assert extraction["text_page_count"] == 1
    pages = await pipeline.pages(document.id)
    assert [p.metadata["has_text"] for p in pages] == [True, False, False]
    assert [p.character_count for p in pages][1:] == [0, 0]


async def test_an_oversized_file_is_refused_before_anything_is_stored(pipeline: Pipeline) -> None:
    data = padded_pdf(pdf_with_pages(["big"]), target_size=LIMITS.max_file_size_bytes + 1)

    with pytest.raises(FileTooLargeError):
        await pipeline.upload(data)

    assert await pipeline.all_documents() == []
    assert pipeline.stored_objects() == []


async def test_too_many_pages_fail_validation(pipeline: Pipeline) -> None:
    document = await pipeline.upload(pdf_with_pages(["p"] * (LIMITS.max_pages_per_document + 1)))

    report = await pipeline.processor.process(document.id)

    assert report.stages == (ProcessingStatus.VALIDATING,)
    stored = await pipeline.document(document.id)
    assert stored.processing_status is ProcessingStatus.FAILED
    assert stored.processing_error == "The PDF has more pages than allowed."
    assert stored.page_count is None
    assert await pipeline.pages(document.id) == []


async def test_duplicate_content_is_refused_at_intake_and_by_the_database(
    pipeline: Pipeline, database: Database
) -> None:
    data = pdf_with_pages(["same bytes"])
    first = await pipeline.upload(data)

    with pytest.raises(DuplicateDocumentError) as excinfo:
        await pipeline.upload(data, filename="another-name.pdf")
    assert excinfo.value.existing_document_id == first.id
    assert len(pipeline.stored_objects()) == 1

    # The partial unique index is the last line of defence against a concurrent double upload.
    sneaky = replace(Factories.document(pipeline.owner_id), content_hash=content_hash(data))
    async with database.unit_of_work() as uow:
        with pytest.raises(DuplicateDocumentError) as raced:
            await uow.documents.add(sneaky)
        assert raced.value.existing_document_id == first.id

    # A deleted document releases its content for re-upload.
    async with database.unit_of_work() as uow:
        stored = await uow.documents.get(pipeline.owner_id, first.id)
        assert stored is not None
        deleted = stored.transition_to(ProcessingStatus.DELETING, now=stored.updated_at)
        deleted = deleted.transition_to(ProcessingStatus.DELETED, now=stored.updated_at)
        assert await uow.documents.compare_and_update(
            deleted, expected_status=ProcessingStatus.UPLOADED
        )
        await uow.commit()
    again = await pipeline.upload(data)
    assert again.id != first.id


async def test_processing_is_idempotent_and_resumable(
    pipeline: Pipeline, database: Database
) -> None:
    document = await pipeline.upload(pdf_with_pages(["one", "two"]))
    first = await pipeline.processor.process(document.id)
    original_ids = {p.id for p in await pipeline.pages(document.id)}

    second = await pipeline.processor.process(document.id)

    assert first.outcome is ProcessingOutcome.PROCESSED
    assert second.outcome is ProcessingOutcome.NO_OP
    assert {p.id for p in await pipeline.pages(document.id)} == original_ids

    # A crash mid-extraction leaves EXTRACTING behind; the next run redoes only that stage and
    # replaces the pages exactly once.
    async with database.unit_of_work() as uow:
        stored = await uow.documents.get(pipeline.owner_id, document.id)
        assert stored is not None
        assert await uow.documents.compare_and_update(
            replace(stored, processing_status=ProcessingStatus.EXTRACTING),
            expected_status=ProcessingStatus.EMBEDDING,
        )
        await uow.commit()

    resumed = await pipeline.processor.process(document.id)

    assert resumed.stages == (ProcessingStatus.EXTRACTING, ProcessingStatus.CHUNKING)
    pages = await pipeline.pages(document.id)
    assert [p.page_number for p in pages] == [1, 2]
    assert {p.id for p in pages}.isdisjoint(original_ids)


async def test_compare_and_update_refuses_a_stale_expectation(
    pipeline: Pipeline, database: Database
) -> None:
    document = await pipeline.upload(pdf_with_pages(["x"]))

    async with database.unit_of_work() as uow:
        moved = document.transition_to(ProcessingStatus.VALIDATING, now=document.updated_at)
        assert not await uow.documents.compare_and_update(
            moved, expected_status=ProcessingStatus.EXTRACTING
        )
        assert await uow.documents.compare_and_update(
            moved, expected_status=ProcessingStatus.UPLOADED
        )
        await uow.commit()

    assert (await pipeline.document(document.id)).processing_status is ProcessingStatus.VALIDATING


async def test_rechunking_upserts_by_stable_id_and_removes_stale_chunks(
    pipeline: Pipeline, database: Database
) -> None:
    document = await pipeline.upload(pdf_with_pages(["alpha beta gamma", "delta"]))
    await pipeline.processor.process(document.id)
    before = await pipeline.chunks(document.id)
    assert [c.chunk_index for c in before] == [0, 1]

    # Re-chunk with a configuration that yields fewer chunks: ids of surviving indexes are kept.
    coarse = DocumentProcessor(
        unit_of_work=database.unit_of_work,
        storage=pipeline.storage,
        extractor=pipeline.processor._extractor,  # noqa: SLF001 - reuse the isolated parser
        chunker=DocumentChunker(ChunkingConfig(chunk_size=512, chunk_overlap=0, min_chunk_size=1)),
        limits=LIMITS,
    )
    async with database.unit_of_work() as uow:
        stored = await uow.documents.get(pipeline.owner_id, document.id)
        assert stored is not None
        assert await uow.documents.compare_and_update(
            replace(stored, processing_status=ProcessingStatus.CHUNKING),
            expected_status=ProcessingStatus.EMBEDDING,
        )
        await uow.commit()

    report = await coarse.process(document.id)

    assert report.stages == (ProcessingStatus.CHUNKING,)
    after = await pipeline.chunks(document.id)
    assert [c.chunk_index for c in after] == [0, 1]  # one chunk per page: same indexes
    assert [c.id for c in after] == [c.id for c in before]
    assert after[0].metadata["chunking"]["chunk_size"] == 512  # type: ignore[index]


async def test_concurrent_uploads_cannot_exceed_the_per_user_limit(
    pipeline: Pipeline, database: Database
) -> None:
    """The row lock serialises the quota check with the insert (§10.2, §49)."""
    limited = DocumentIntakeService(
        unit_of_work=database.unit_of_work,
        storage=pipeline.storage,
        limits=UploadLimits(
            max_file_size_bytes=LIMITS.max_file_size_bytes,
            max_pages_per_document=LIMITS.max_pages_per_document,
            max_documents_per_user=1,
        ),
    )
    uploads = [pdf_with_pages([f"document {index}"]) for index in range(4)]

    outcomes = await asyncio.gather(
        *(
            limited.accept(
                pipeline.owner_id,
                filename=f"{index}.pdf",
                declared_mime_type=PDF_MIME,
                data=data,
            )
            for index, data in enumerate(uploads)
        ),
        return_exceptions=True,
    )

    accepted = [o for o in outcomes if isinstance(o, Document)]
    refused = [o for o in outcomes if isinstance(o, DocumentLimitReachedError)]
    kinds = [
        type(o).__name__ + (f": {o}" if isinstance(o, BaseException) else "") for o in outcomes
    ]
    assert len(accepted) == 1, kinds
    assert len(refused) == 3, kinds
    assert len(await pipeline.all_documents()) == 1
    assert len(pipeline.stored_objects()) == 1  # refused uploads removed their objects
