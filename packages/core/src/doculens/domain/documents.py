"""Document, page and chunk entities and the processing state machine (§7.3 to §7.5, §12).

A document's ``collection_id`` is optional: §30 allows removing a document from its collection
without deleting it. The full conversation-scope model is still open (`OQ-8`).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from doculens.domain.errors import ConflictError, NotFoundError

MAX_FILENAME_LENGTH = 255


class DocumentNotFoundError(NotFoundError):
    """Also raised for documents owned by someone else: existence is never disclosed (§9)."""

    code = "DOCUMENT_NOT_FOUND"
    default_message = "The document was not found."


class ProcessingStatus(StrEnum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    EXTRACTING = "EXTRACTING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETED = "DELETED"


ALLOWED_TRANSITIONS: Mapping[ProcessingStatus, frozenset[ProcessingStatus]] = {
    ProcessingStatus.UPLOADED: frozenset({ProcessingStatus.VALIDATING, ProcessingStatus.DELETING}),
    ProcessingStatus.VALIDATING: frozenset({ProcessingStatus.EXTRACTING, ProcessingStatus.FAILED}),
    ProcessingStatus.EXTRACTING: frozenset({ProcessingStatus.CHUNKING, ProcessingStatus.FAILED}),
    ProcessingStatus.CHUNKING: frozenset({ProcessingStatus.EMBEDDING, ProcessingStatus.FAILED}),
    ProcessingStatus.EMBEDDING: frozenset({ProcessingStatus.INDEXING, ProcessingStatus.FAILED}),
    ProcessingStatus.INDEXING: frozenset({ProcessingStatus.READY, ProcessingStatus.FAILED}),
    # Reprocessing (§29, §32) restarts the pipeline; the exact reprocess/re-index split is OQ-5.
    ProcessingStatus.READY: frozenset({ProcessingStatus.VALIDATING, ProcessingStatus.DELETING}),
    ProcessingStatus.FAILED: frozenset({ProcessingStatus.VALIDATING, ProcessingStatus.DELETING}),
    ProcessingStatus.DELETING: frozenset({ProcessingStatus.DELETED}),
    ProcessingStatus.DELETED: frozenset(),
}


class InvalidStatusTransitionError(ConflictError):
    code = "INVALID_STATUS_TRANSITION"
    default_message = "The document cannot move to the requested processing state."


@dataclass(frozen=True, slots=True)
class Document:
    id: UUID
    owner_id: UUID
    collection_id: UUID | None
    filename: str
    storage_key: str
    content_hash: str
    mime_type: str
    file_size: int
    processing_status: ProcessingStatus
    created_at: datetime
    updated_at: datetime
    page_count: int | None = None
    processing_error: str | None = None
    chunk_count: int = 0
    indexed_at: datetime | None = None
    # Facts learnt from the file itself (§13 document metadata, extraction summary); not in the
    # §7.3 field list, added under OQ-14 (see ADR-015).
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.processing_status is ProcessingStatus.READY

    def with_extraction(
        self, *, page_count: int, metadata: Mapping[str, object], now: datetime
    ) -> Self:
        """Record what extraction learnt; the status transition is a separate, explicit step."""
        return replace(self, page_count=page_count, metadata=dict(metadata), updated_at=now)

    def with_chunking(
        self, *, chunk_count: int, metadata: Mapping[str, object], now: datetime
    ) -> Self:
        """Record the chunk set; the status transition is a separate, explicit step."""
        return replace(self, chunk_count=chunk_count, metadata=dict(metadata), updated_at=now)

    def with_indexing(
        self, *, chunk_count: int, metadata: Mapping[str, object], now: datetime
    ) -> Self:
        """Record a completed indexing run; the status transition is a separate step."""
        return replace(
            self,
            chunk_count=chunk_count,
            metadata=dict(metadata),
            indexed_at=now,
            updated_at=now,
        )

    def transition_to(self, status: ProcessingStatus, *, now: datetime) -> Self:
        """Return a copy in ``status`` if the transition is allowed by §7.3, else raise."""
        if status not in ALLOWED_TRANSITIONS[self.processing_status]:
            message = f"cannot move from {self.processing_status} to {status}"
            raise InvalidStatusTransitionError(message)
        cleared_error = None if status is not ProcessingStatus.FAILED else self.processing_error
        return replace(
            self, processing_status=status, processing_error=cleared_error, updated_at=now
        )

    def mark_failed(self, safe_message: str, *, now: datetime) -> Self:
        """Move to FAILED with a message that is safe to show to the user (§12)."""
        failed = self.transition_to(ProcessingStatus.FAILED, now=now)
        return replace(failed, processing_error=safe_message)


@dataclass(frozen=True, slots=True)
class DocumentPage:
    id: UUID
    document_id: UUID
    page_number: int
    extracted_text: str
    character_count: int
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: UUID
    document_id: UUID
    page_id: UUID
    chunk_index: int
    text: str
    token_count: int
    metadata: Mapping[str, object]
    vector_id: str | None = None


__all__ = [
    "ALLOWED_TRANSITIONS",
    "MAX_FILENAME_LENGTH",
    "Document",
    "DocumentChunk",
    "DocumentNotFoundError",
    "DocumentPage",
    "InvalidStatusTransitionError",
    "ProcessingStatus",
]
