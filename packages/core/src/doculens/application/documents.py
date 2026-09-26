"""Document metadata and lifecycle use cases (SPECIFICATIONS.md §7.3, §9, §29, §31, §32).

Listing, inspection, filename search, renaming and moving between collections, plus the
operations that change a document's processing life: reprocess, re-index and delete.

Ownership is enforced through owner-scoped repository reads; a document or target collection
owned by someone else is reported as not found (§9). A ``DELETED`` tombstone is invisible to
every read except the delete saga, which treats a second delete as success (§31, ADR-019).

Deletion is a resumable saga: ``DELETING`` → vectors → object store → pages and chunks →
``DELETED``. Each external step is idempotent, so a partial failure stays ``DELETING`` and the
next call finishes the work. Citations keep ``document_id`` and lose only ``chunk_id``.
"""

import logging
from dataclasses import replace
from typing import Protocol
from uuid import UUID

from doculens.application.auth import Clock
from doculens.application.storage import ObjectStorage
from doculens.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from doculens.application.vectors import VectorStore
from doculens.domain.collections import CollectionNotFoundError
from doculens.domain.common import UNSET, Unset, clean_label
from doculens.domain.documents import (
    MAX_FILENAME_LENGTH,
    Document,
    DocumentCannotReindexError,
    DocumentDeletionInProgressError,
    DocumentNotFoundError,
    InvalidStatusTransitionError,
    ProcessingStatus,
)
from doculens.domain.errors import InvalidInputError
from doculens.domain.storage import StorageUnavailableError
from doculens.domain.time import utc_now
from doculens.domain.vectors import VectorStoreError, VectorStoreUnavailableError

logger = logging.getLogger(__name__)


class DocumentJobSink(Protocol):
    async def enqueue_quietly(
        self, document_id: UUID, *, request_id: str | None = None
    ) -> object: ...


def clean_filename(filename: str) -> str:
    cleaned = clean_label(filename, field="filename", max_length=MAX_FILENAME_LENGTH)
    if "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."}:
        message = "The filename must not contain path separators."
        raise InvalidInputError(message)
    return cleaned


async def ensure_collection_owned(uow: UnitOfWork, owner_id: UUID, collection_id: UUID) -> None:
    if await uow.collections.get(owner_id, collection_id) is None:
        raise CollectionNotFoundError


class DocumentService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        vectors: VectorStore | None = None,
        storage: ObjectStorage | None = None,
        jobs: DocumentJobSink | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._vectors = vectors
        self._storage = storage
        self._jobs = jobs
        self._clock = clock

    async def list_for_owner(
        self, owner_id: UUID, *, collection_id: UUID | None = None, query: str | None = None
    ) -> list[Document]:
        """The owner's live documents, optionally restricted to one collection and to a
        filename search (§29). A blank query is treated as no query rather than as
        "matches everything". Tombstones never appear."""
        search = query.strip() if query is not None else None
        async with self._unit_of_work() as uow:
            if collection_id is not None:
                await ensure_collection_owned(uow, owner_id, collection_id)
            if search:
                return await uow.documents.search_for_owner(
                    owner_id, query=search, collection_id=collection_id
                )
            if collection_id is None:
                return await uow.documents.list_for_owner(owner_id)
            return await uow.documents.list_in_collection(owner_id, collection_id)

    async def get(self, owner_id: UUID, document_id: UUID) -> Document:
        async with self._unit_of_work() as uow:
            return await self._require_live(uow, owner_id, document_id)

    async def update(
        self,
        owner_id: UUID,
        document_id: UUID,
        *,
        filename: str | Unset = UNSET,
        collection_id: UUID | Unset | None = UNSET,
    ) -> Document:
        async with self._unit_of_work() as uow:
            current = await self._require_live(uow, owner_id, document_id)
            if current.is_deleting:
                raise DocumentDeletionInProgressError
            if isinstance(collection_id, Unset):
                target_collection = current.collection_id
            else:
                if collection_id is not None:
                    await ensure_collection_owned(uow, owner_id, collection_id)
                target_collection = collection_id
            updated = replace(
                current,
                filename=(
                    current.filename if isinstance(filename, Unset) else clean_filename(filename)
                ),
                collection_id=target_collection,
                updated_at=self._clock(),
            )
            # Compare-and-set so a concurrent delete cannot be overwritten back to READY
            # (or any other live status) by a rename/move.
            if not await uow.documents.compare_and_update(
                updated, expected_status=current.processing_status
            ):
                await uow.rollback()
                raced = await uow.documents.get(owner_id, document_id)
                if raced is None or raced.is_deleted:
                    raise DocumentNotFoundError
                if raced.is_deleting:
                    raise DocumentDeletionInProgressError
                raise InvalidStatusTransitionError
            await uow.commit()
        if self._vectors is not None and updated.collection_id != current.collection_id:
            # PostgreSQL is authoritative; the vectors carry the collection for filtering (§16)
            # and must follow the move, otherwise scoped retrieval would look in the old place.
            try:
                await self._vectors.set_document_collection(
                    owner_id, document_id, updated.collection_id
                )
            except (VectorStoreUnavailableError, VectorStoreError):
                await self._revert_collection_id(
                    owner_id,
                    document_id,
                    collection_id=current.collection_id,
                    expected_status=updated.processing_status,
                )
                raise
        return updated

    async def _revert_collection_id(
        self,
        owner_id: UUID,
        document_id: UUID,
        *,
        collection_id: UUID | None,
        expected_status: ProcessingStatus,
    ) -> None:
        """Undo a committed collection move when the vector store could not follow it."""
        async with self._unit_of_work() as uow:
            latest = await uow.documents.get(owner_id, document_id)
            if latest is None or latest.is_deleted or latest.is_deleting:
                return
            if latest.processing_status is not expected_status:
                return
            reverted = replace(latest, collection_id=collection_id, updated_at=self._clock())
            await uow.documents.compare_and_update(reverted, expected_status=expected_status)
            await uow.commit()

    async def reprocess(self, owner_id: UUID, document_id: UUID) -> Document:
        """Restart the pipeline from the stored original (ADR-019): ``VALIDATING`` onwards."""
        return await self._restart(owner_id, document_id, ProcessingStatus.VALIDATING)

    async def reindex(self, owner_id: UUID, document_id: UUID) -> Document:
        """Re-chunk and re-embed from stored pages, skipping extraction (ADR-019)."""
        return await self._restart(owner_id, document_id, ProcessingStatus.CHUNKING)

    async def delete(self, owner_id: UUID, document_id: UUID) -> None:
        """Remove the document's content from every store; a second call is a no-op (§31)."""
        document = await self._begin_deletion(owner_id, document_id)
        if document is None:
            return
        await self._purge_vectors(document)
        await self._purge_object(document)
        await self._finish_deletion(document)
        logger.info(
            "document deleted",
            extra={
                "operation": "document.delete",
                "user_id": str(owner_id),
                "document_id": str(document_id),
            },
        )

    async def _restart(
        self, owner_id: UUID, document_id: UUID, target: ProcessingStatus
    ) -> Document:
        async with self._unit_of_work() as uow:
            current = await self._require_live(uow, owner_id, document_id)
            if current.is_deleting:
                raise DocumentDeletionInProgressError
            if target is ProcessingStatus.CHUNKING:
                pages = await uow.document_content.list_pages(owner_id, document_id)
                if not pages:
                    raise DocumentCannotReindexError
            if current.processing_status is target:
                return current
            # UPLOADED is already waiting for a full run; do not invent a new start.
            if (
                current.processing_status is ProcessingStatus.UPLOADED
                and target is ProcessingStatus.VALIDATING
            ):
                return current
            restarted = current.transition_to(target, now=self._clock())
            if not await uow.documents.compare_and_update(
                restarted, expected_status=current.processing_status
            ):
                raise InvalidStatusTransitionError
            await uow.commit()
        if self._jobs is not None:
            await self._jobs.enqueue_quietly(document_id)
        logger.info(
            "document processing restarted",
            extra={
                "operation": "document.restart",
                "user_id": str(owner_id),
                "document_id": str(document_id),
                "from_status": current.processing_status.value,
                "to_status": target.value,
            },
        )
        return restarted

    async def _begin_deletion(self, owner_id: UUID, document_id: UUID) -> Document | None:
        """Move the row to ``DELETING``, or return ``None`` when it is already a tombstone."""
        async with self._unit_of_work() as uow:
            document = await uow.documents.get(owner_id, document_id)
            if document is None:
                raise DocumentNotFoundError
            if document.is_deleted:
                return None
            if document.is_deleting:
                return document
            deleting = document.transition_to(ProcessingStatus.DELETING, now=self._clock())
            if not await uow.documents.compare_and_update(
                deleting, expected_status=document.processing_status
            ):
                await uow.rollback()
                raced = await uow.documents.get(owner_id, document_id)
                if raced is None:
                    raise DocumentNotFoundError
                if raced.is_deleted:
                    return None
                if raced.is_deleting:
                    return raced
                raise InvalidStatusTransitionError
            await uow.commit()
            return deleting

    async def _purge_vectors(self, document: Document) -> None:
        if self._vectors is None:
            return
        try:
            await self._vectors.delete_document(document.owner_id, document.id)
        except (VectorStoreUnavailableError, VectorStoreError) as exc:
            logger.warning(
                "document vector purge failed",
                extra={
                    "operation": "document.delete_vectors",
                    "document_id": str(document.id),
                    "error_code": exc.code,
                },
            )
            raise VectorStoreUnavailableError from exc

    async def _purge_object(self, document: Document) -> None:
        if self._storage is None:
            return
        try:
            await self._storage.delete(document.storage_key)
        except StorageUnavailableError:
            logger.warning(
                "document object purge failed",
                extra={
                    "operation": "document.delete_object",
                    "document_id": str(document.id),
                    "object_key": document.storage_key,
                },
            )
            raise

    async def _finish_deletion(self, document: Document) -> None:
        tombstone = document.as_deleted(now=self._clock())
        async with self._unit_of_work() as uow:
            await uow.document_content.delete_content(document.id)
            if not await uow.documents.compare_and_update(
                tombstone, expected_status=ProcessingStatus.DELETING
            ):
                current = await uow.documents.get(document.owner_id, document.id)
                if current is not None and current.is_deleted:
                    await uow.rollback()
                    return
                raise InvalidStatusTransitionError
            await uow.commit()

    async def _require_live(self, uow: UnitOfWork, owner_id: UUID, document_id: UUID) -> Document:
        document = await uow.documents.get(owner_id, document_id)
        if document is None or document.is_deleted:
            raise DocumentNotFoundError
        return document
