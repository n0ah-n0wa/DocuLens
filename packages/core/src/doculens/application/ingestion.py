"""Document ingestion use cases (SPECIFICATIONS.md §10 to §13, §31, §48, §49, §67).

``DocumentIntakeService`` is the synchronous half: it validates what can be validated without a
parser, refuses duplicates and over-quota uploads, stores the original and creates the document
row in ``UPLOADED``. ``DocumentProcessor`` is the asynchronous half run by the worker: it walks
the document through the §7.3 states one stage at a time.

Processing is idempotent and safe under at-least-once delivery (§48, §49):

- every state change is a compare-and-set on the current status, so two workers holding the same
  job cannot both advance a document, and a job for a document that already moved on is a no-op;
- a run resumes from the document's current stage, so a crash mid-stage is repaired by the next
  delivery without redoing earlier stages;
- extraction replaces the document's pages inside one transaction, so a resumed or repeated
  extraction never leaves duplicate or partial pages;
- rejections of the file itself end in ``FAILED`` with a safe message; infrastructure failures
  (storage, database) propagate unchanged so the job is retried instead of failing the document.

``EMBEDDING`` turns the persisted chunks into vectors through the ``EmbeddingProvider`` and
writes them to the ``VectorStore`` under the chunks' stable ids, one bounded window of chunks at
a time so memory does not grow with the document; a chunk whose vector already exists with the
same content hash and model is skipped, which makes a resumed or repeated run cheap and keeps a
changed chunk (after re-extraction) or a changed model from serving a stale vector. ``INDEXING``
prunes vectors that no chunk claims any more, records each chunk's ``vector_id`` and moves the
document to ``READY``. Until then the document is not ``READY`` and retrieval must not serve it,
so a partially written index is never observable.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from doculens.application.chunking import DocumentChunker
from doculens.application.documents import clean_filename, ensure_collection_owned
from doculens.application.embeddings import EmbeddingProvider
from doculens.application.storage import ObjectStorage
from doculens.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from doculens.application.vectors import VectorStore, records_for_chunks
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.embeddings import EmbeddingError, EmbeddingUsage
from doculens.domain.errors import ConflictError, DependencyUnavailableError, InvalidInputError
from doculens.domain.ids import new_id
from doculens.domain.ingestion import (
    PDF_MIME_TYPE,
    DocumentLimitReachedError,
    DuplicateDocumentError,
    EmbeddingFailedError,
    ExtractionFailedError,
    ExtractionResult,
    FileTooLargeError,
    IndexingFailedError,
    InvalidFileSignatureError,
    PdfInfo,
    PdfRejectedError,
    StoredFileMismatchError,
    StoredFileMissingError,
    TooManyPagesError,
    UploadLimits,
    has_pdf_signature,
    ordered_pages,
    validate_upload,
)
from doculens.domain.storage import ObjectNotFoundError, content_hash, document_object_key
from doculens.domain.time import utc_now
from doculens.domain.vectors import VectorMetadata, VectorStoreError, vector_id_for

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


class PdfExtractor(Protocol):
    """Parsing port. Both operations raise ``PdfRejectedError`` subclasses for unusable files."""

    async def inspect(self, data: bytes, *, max_pages: int) -> PdfInfo:
        """Open the file and report its page count and metadata without reading page text."""
        ...

    async def extract(self, data: bytes, *, max_pages: int) -> ExtractionResult:
        """Extract every page's text in order; pages without text are included."""
        ...


class DocumentIntakeService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        storage: ObjectStorage,
        limits: UploadLimits,
        clock: Clock = utc_now,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._limits = limits
        self._clock = clock

    async def accept(
        self,
        owner_id: UUID,
        *,
        filename: str,
        declared_mime_type: str,
        data: bytes,
        collection_id: UUID | None = None,
    ) -> Document:
        """Validate, store and register an upload; nothing persists unless every step succeeds."""
        name = clean_filename(filename)
        validate_upload(
            filename=name, declared_mime_type=declared_mime_type, data=data, limits=self._limits
        )
        digest = content_hash(data)
        # A cheap pre-check answers the common failures before any bytes are stored; the same
        # checks run again under the user's row lock when the row is written.
        async with self._unit_of_work() as uow:
            if collection_id is not None:
                await ensure_collection_owned(uow, owner_id, collection_id)
            await self._check_limits(uow, owner_id, digest)

        now = self._clock()
        document_id = new_id()
        key = document_object_key(owner_id, document_id)
        document = Document(
            id=document_id,
            owner_id=owner_id,
            collection_id=collection_id,
            filename=name,
            storage_key=key,
            content_hash=digest,
            mime_type=PDF_MIME_TYPE,
            file_size=len(data),
            processing_status=ProcessingStatus.UPLOADED,
            created_at=now,
            updated_at=now,
        )
        # The original is stored before the row exists (§12 ordering); if the row cannot be
        # written the object is removed again so nothing dangles.
        await self._storage.put(key, data, content_type=PDF_MIME_TYPE)
        try:
            async with self._unit_of_work() as uow:
                # The lock serialises concurrent uploads by one user so the per-user limit and
                # the duplicate rule hold under concurrency (the unique index is the backstop).
                await uow.users.lock(owner_id)
                await self._check_limits(uow, owner_id, digest)
                await uow.documents.add(document)
                await uow.commit()
        except BaseException:
            await self._discard_quietly(key)
            raise
        logger.info(
            "document accepted",
            extra={
                "operation": "ingestion.accept",
                "document_id": str(document.id),
                "user_id": str(owner_id),
                "file_size": len(data),
            },
        )
        return document

    async def _check_limits(self, uow: UnitOfWork, owner_id: UUID, digest: str) -> None:
        existing = await uow.documents.find_by_content_hash(owner_id, digest)
        if existing is not None:
            raise DuplicateDocumentError(existing.id)
        if await uow.documents.count_for_owner(owner_id) >= self._limits.max_documents_per_user:
            raise DocumentLimitReachedError

    async def _discard_quietly(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except Exception:  # noqa: BLE001 - best effort; the original error is what matters
            logger.warning(
                "could not remove the object of a failed intake",
                extra={"operation": "ingestion.cleanup_failed", "object_key": key},
            )


class ProcessingOutcome(StrEnum):
    PROCESSED = "processed"  # advanced through at least one stage
    FAILED = "failed"  # the file was rejected; the document is FAILED
    NO_OP = "no_op"  # nothing left for this pipeline to do (already past its stages)
    CONCURRENT = "concurrent"  # another worker moved the document; nothing changed here
    SKIPPED = "skipped"  # unknown document, or one being deleted


@dataclass(frozen=True, slots=True)
class ProcessingReport:
    document_id: UUID
    outcome: ProcessingOutcome
    status: ProcessingStatus | None
    stages: tuple[ProcessingStatus, ...] = ()


class DomainErrorLike(Protocol):
    code: str
    message: str
    diagnostics: str | None


@dataclass(frozen=True, slots=True)
class _Embedded:
    """What the embedding stage established: the vector id of every chunk and what it cost."""

    vector_ids: dict[UUID, str]
    usage: EmbeddingUsage
    embedded_now: int


class _LostRaceError(Exception):
    """Internal: a compare-and-set found the document in another state."""


class DocumentProcessor:
    """Runs the implemented stages (validation, extraction) for one document."""

    def __init__(  # noqa: PLR0913 - every port the pipeline uses, wired by the composition root
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        storage: ObjectStorage,
        extractor: PdfExtractor,
        chunker: DocumentChunker,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        limits: UploadLimits,
        clock: Clock = utc_now,
        index_window: int = 128,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._extractor = extractor
        self._chunker = chunker
        self._embeddings = embeddings
        self._vectors = vectors
        self._limits = limits
        self._clock = clock
        self._index_window = max(1, index_window)

    async def process(self, document_id: UUID) -> ProcessingReport:
        async with self._unit_of_work() as uow:
            document = await uow.documents.get_for_processing(document_id)
        if document is None:
            logger.warning(
                "processing requested for an unknown document",
                extra={"operation": "ingestion.skip", "document_id": str(document_id)},
            )
            return ProcessingReport(document_id, ProcessingOutcome.SKIPPED, None)
        if document.processing_status in (ProcessingStatus.DELETING, ProcessingStatus.DELETED):
            return ProcessingReport(
                document_id, ProcessingOutcome.SKIPPED, document.processing_status
            )

        stages: list[ProcessingStatus] = []
        data: bytes | None = None
        try:
            if document.processing_status is ProcessingStatus.UPLOADED:
                document = await self._advance(document, ProcessingStatus.VALIDATING)
            if document.processing_status is ProcessingStatus.VALIDATING:
                stages.append(ProcessingStatus.VALIDATING)
                document, data = await self._validate(document)
            if document.processing_status is ProcessingStatus.EXTRACTING:
                stages.append(ProcessingStatus.EXTRACTING)
                document = await self._extract(document, data)
            if document.processing_status is ProcessingStatus.CHUNKING:
                stages.append(ProcessingStatus.CHUNKING)
                document = await self._chunk(document)
            embedded: _Embedded | None = None
            if document.processing_status is ProcessingStatus.EMBEDDING:
                stages.append(ProcessingStatus.EMBEDDING)
                document, embedded = await self._embed(document)
            if document.processing_status is ProcessingStatus.INDEXING:
                stages.append(ProcessingStatus.INDEXING)
                document = await self._index(document, embedded)
        except _LostRaceError:
            logger.info(
                "document was moved by another worker",
                extra={"operation": "ingestion.concurrent", "document_id": str(document_id)},
            )
            return ProcessingReport(document_id, ProcessingOutcome.CONCURRENT, None)

        if not stages:
            outcome = ProcessingOutcome.NO_OP
        elif document.processing_status is ProcessingStatus.FAILED:
            outcome = ProcessingOutcome.FAILED
        else:
            outcome = ProcessingOutcome.PROCESSED
        return ProcessingReport(document_id, outcome, document.processing_status, tuple(stages))

    # -- stages ------------------------------------------------------------------------------------

    async def _validate(self, document: Document) -> tuple[Document, bytes | None]:
        try:
            data = await self._fetch(document)
            info = await self._extractor.inspect(
                data, max_pages=self._limits.max_pages_per_document
            )
            self._ensure_page_limit(info.page_count)
        except PdfRejectedError as error:
            return await self._fail(document, error), None
        except (DependencyUnavailableError, _LostRaceError):
            raise
        except Exception as error:  # noqa: BLE001 - any other failure ends in FAILED (§12)
            return await self._fail(document, _unexpected(error)), None
        return await self._advance(document, ProcessingStatus.EXTRACTING), data

    async def _extract(self, document: Document, data: bytes | None) -> Document:
        try:
            if data is None:
                data = await self._fetch(document)
            result = await self._extractor.extract(
                data, max_pages=self._limits.max_pages_per_document
            )
            pages = [
                page.to_document_page(document.id, new_id()) for page in ordered_pages(result.pages)
            ]
            return await self._persist_extraction(document, result, pages)
        except PdfRejectedError as error:
            return await self._fail(document, error)
        except (DependencyUnavailableError, _LostRaceError):
            raise
        except ConflictError as error:
            # Another run's pages landed first (unique page numbers): treat as a lost race.
            raise _LostRaceError from error
        except Exception as error:  # noqa: BLE001 - any other failure ends in FAILED (§12)
            return await self._fail(document, _unexpected(error))

    async def _persist_extraction(
        self, document: Document, result: ExtractionResult, pages: list[DocumentPage]
    ) -> Document:
        now = self._clock()
        metadata: dict[str, object] = {
            # A re-extraction invalidates everything derived from the previous pages.
            **{key: value for key, value in document.metadata.items() if key != "chunking"},
            "pdf": result.info.metadata.as_mapping(),
            "extraction": {
                "page_count": len(pages),
                "text_page_count": result.text_page_count,
                "empty_page_count": result.empty_page_count,
                "extracted_at": now.isoformat(),
            },
        }
        updated = document.with_extraction(page_count=len(pages), metadata=metadata, now=now)
        updated = updated.transition_to(ProcessingStatus.CHUNKING, now=now)
        async with self._unit_of_work() as uow:
            await uow.document_content.delete_content(document.id)
            await uow.document_content.add_pages(pages)
            if not await uow.documents.compare_and_update(
                updated, expected_status=document.processing_status
            ):
                await uow.rollback()
                raise _LostRaceError
            await uow.commit()
        logger.info(
            "document extracted",
            extra={
                "operation": "ingestion.extracted",
                "document_id": str(document.id),
                "page_count": len(pages),
                "empty_page_count": result.empty_page_count,
            },
        )
        return updated

    async def _chunk(self, document: Document) -> Document:
        try:
            async with self._unit_of_work() as uow:
                pages = await uow.document_content.list_pages(document.owner_id, document.id)
            chunks = self._chunker.chunk(document.id, pages)
            now = self._clock()
            metadata: dict[str, object] = {
                **document.metadata,
                "chunking": {
                    **self._chunker.describe(),
                    "chunk_count": len(chunks),
                    "chunked_at": now.isoformat(),
                },
            }
            updated = document.with_chunking(chunk_count=len(chunks), metadata=metadata, now=now)
            updated = updated.transition_to(ProcessingStatus.EMBEDDING, now=now)
            await self._persist_chunks(document, updated, chunks)
        except (DependencyUnavailableError, _LostRaceError):
            raise
        except ConflictError as error:
            raise _LostRaceError from error
        except Exception as error:  # noqa: BLE001 - any other failure ends in FAILED (§12)
            return await self._fail(document, _unexpected(error))
        logger.info(
            "document chunked",
            extra={
                "operation": "ingestion.chunked",
                "document_id": str(document.id),
                "chunk_count": len(chunks),
            },
        )
        return updated

    async def _embed(self, document: Document) -> tuple[Document, "_Embedded | None"]:
        try:
            embedded = await self._ensure_vectors(document)
        except (DependencyUnavailableError, _LostRaceError):
            raise
        except (EmbeddingError, InvalidInputError) as error:
            failed = EmbeddingFailedError(diagnostics=_diagnostics_of(error))
            return await self._fail(document, failed), None
        except VectorStoreError as error:
            rejected = IndexingFailedError(diagnostics=_diagnostics_of(error))
            return await self._fail(document, rejected), None
        except Exception as error:  # noqa: BLE001 - any other failure ends in FAILED (§12)
            return await self._fail(document, _unexpected(error)), None
        logger.info(
            "document embedded",
            extra={
                "operation": "ingestion.embedded",
                "document_id": str(document.id),
                "chunk_count": len(embedded.vector_ids),
                "embedded_now": embedded.embedded_now,
                "embedding_requests": embedded.usage.requests,
                "embedding_tokens": embedded.usage.tokens,
                "embedding_latency_ms": round(embedded.usage.latency_ms, 1),
            },
        )
        return await self._advance(document, ProcessingStatus.INDEXING), embedded

    async def _index(self, document: Document, embedded: "_Embedded | None") -> Document:
        try:
            if embedded is None:
                # Resumed after a crash: vectors written before the crash are found in the store
                # and only the missing or changed ones are embedded again.
                embedded = await self._ensure_vectors(document)
            removed = await self._vectors.prune_document(
                document.owner_id, document.id, keep=embedded.vector_ids.values()
            )
            return await self._persist_index(document, embedded, removed)
        except (DependencyUnavailableError, _LostRaceError):
            raise
        except (EmbeddingError, InvalidInputError) as error:
            return await self._fail(
                document, EmbeddingFailedError(diagnostics=_diagnostics_of(error))
            )
        except VectorStoreError as error:
            return await self._fail(
                document, IndexingFailedError(diagnostics=_diagnostics_of(error))
            )
        except ConflictError as error:
            raise _LostRaceError from error
        except Exception as error:  # noqa: BLE001 - any other failure ends in FAILED (§12)
            return await self._fail(document, _unexpected(error))

    async def _ensure_vectors(self, document: Document) -> "_Embedded":
        """Bring the store to one current vector per chunk, a bounded window at a time."""
        async with self._unit_of_work() as uow:
            chunks = await uow.document_content.list_chunks(document.owner_id, document.id)
        vector_ids = {chunk.id: vector_id_for(chunk.id) for chunk in chunks}
        if not chunks:
            return _Embedded(vector_ids, EmbeddingUsage(), embedded_now=0)
        present = await self._vectors.describe_document(document.owner_id, document.id)
        pending = [
            chunk
            for chunk in chunks
            if not self._is_current(present.get(vector_ids[chunk.id]), chunk)
        ]
        usage = EmbeddingUsage()
        for start in range(0, len(pending), self._index_window):
            window = pending[start : start + self._index_window]
            result = await self._embeddings.embed_documents([chunk.text for chunk in window])
            records = records_for_chunks(
                window,
                result,
                owner_id=document.owner_id,
                collection_id=document.collection_id,
                embedding_provider=self._embeddings.name,
            )
            await self._vectors.upsert(document.owner_id, records)
            usage += result.usage
        return _Embedded(vector_ids, usage, embedded_now=len(pending))

    def _is_current(self, existing: VectorMetadata | None, chunk: DocumentChunk) -> bool:
        """A stored vector is reused only for unchanged text under the same model and provider."""
        return (
            existing is not None
            and existing.content_hash == str(chunk.metadata.get("content_hash", ""))
            and existing.embedding_model == self._embeddings.model
            and existing.embedding_provider == self._embeddings.name
        )

    async def _persist_index(
        self, document: Document, embedded: "_Embedded", removed: int
    ) -> Document:
        now = self._clock()
        metadata: dict[str, object] = {
            **document.metadata,
            "indexing": {
                "vector_store": self._vectors.collection,
                "embedding_provider": self._embeddings.name,
                "embedding_model": self._embeddings.model,
                "vector_count": len(embedded.vector_ids),
                "embedded_now": embedded.embedded_now,
                "stale_vectors_removed": removed,
                "embedding_requests": embedded.usage.requests,
                "embedding_tokens": embedded.usage.tokens,
                "embedding_latency_ms": round(embedded.usage.latency_ms, 1),
                "indexed_at": now.isoformat(),
            },
        }
        updated = document.with_indexing(
            chunk_count=len(embedded.vector_ids), metadata=metadata, now=now
        )
        updated = updated.transition_to(ProcessingStatus.READY, now=now)
        async with self._unit_of_work() as uow:
            await uow.document_content.set_vector_ids(document.id, embedded.vector_ids)
            if not await uow.documents.compare_and_update(
                updated, expected_status=document.processing_status
            ):
                await uow.rollback()
                raise _LostRaceError
            await uow.commit()
        logger.info(
            "document indexed",
            extra={
                "operation": "ingestion.indexed",
                "document_id": str(document.id),
                "vector_count": len(embedded.vector_ids),
                "stale_vectors_removed": removed,
            },
        )
        return updated

    async def _persist_chunks(
        self, document: Document, updated: Document, chunks: list[DocumentChunk]
    ) -> None:
        async with self._unit_of_work() as uow:
            await uow.document_content.replace_chunks(document.id, chunks)
            if not await uow.documents.compare_and_update(
                updated, expected_status=document.processing_status
            ):
                await uow.rollback()
                raise _LostRaceError
            await uow.commit()

    # -- helpers -----------------------------------------------------------------------------------

    def _ensure_page_limit(self, page_count: int) -> None:
        limit = self._limits.max_pages_per_document
        if page_count > limit:
            raise TooManyPagesError(diagnostics=f"{page_count} pages, limit {limit}")

    async def _fetch(self, document: Document) -> bytes:
        """The original bytes, re-verified against the row before any parser sees them."""
        try:
            stored = await self._storage.get(document.storage_key)
        except ObjectNotFoundError as exc:
            raise StoredFileMissingError(diagnostics="object missing from storage") from exc
        data = stored.data
        if content_hash(data) != document.content_hash:
            message = "object hash differs from the document row"
            raise StoredFileMismatchError(diagnostics=message)
        if len(data) > self._limits.max_file_size_bytes:
            raise FileTooLargeError
        if not has_pdf_signature(data):
            raise InvalidFileSignatureError
        return data

    async def _advance(self, document: Document, status: ProcessingStatus) -> Document:
        moved = document.transition_to(status, now=self._clock())
        await self._commit_transition(moved, expected=document.processing_status)
        logger.info(
            "document stage started",
            extra={
                "operation": "ingestion.stage",
                "document_id": str(document.id),
                "status": status.value,
            },
        )
        return moved

    async def _fail(self, document: Document, error: DomainErrorLike) -> Document:
        failed = document.mark_failed(error.message, now=self._clock())
        await self._commit_transition(failed, expected=document.processing_status)
        logger.warning(
            "document processing failed",
            extra={
                "operation": "ingestion.failed",
                "document_id": str(document.id),
                "stage": document.processing_status.value,
                "error_code": error.code,
                "diagnostics": error.diagnostics,
            },
        )
        return failed

    async def _commit_transition(self, document: Document, *, expected: ProcessingStatus) -> None:
        async with self._unit_of_work() as uow:
            if not await uow.documents.compare_and_update(document, expected_status=expected):
                raise _LostRaceError
            await uow.commit()


def _diagnostics_of(error: EmbeddingError | VectorStoreError | InvalidInputError) -> str:
    diagnostics = getattr(error, "diagnostics", None)
    return f"{error.code}: {diagnostics or error.message}"


def _unexpected(error: Exception) -> PdfRejectedError:
    logger.error(
        "unexpected failure while processing a document",
        extra={"operation": "ingestion.error", "error_type": type(error).__name__},
        exc_info=error,
    )
    return ExtractionFailedError(diagnostics=f"{type(error).__name__}: {error}")


__all__ = [
    "DocumentIntakeService",
    "DocumentLimitReachedError",
    "DocumentProcessor",
    "DuplicateDocumentError",
    "FileTooLargeError",
    "InvalidFileSignatureError",
    "PdfExtractor",
    "ProcessingOutcome",
    "ProcessingReport",
    "UnitOfWork",
    "UploadLimits",
]
