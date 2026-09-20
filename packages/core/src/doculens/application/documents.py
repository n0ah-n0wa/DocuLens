"""Document metadata use cases (SPECIFICATIONS.md §7.3, §9, §29).

Listing, inspection, renaming and moving between collections. Upload, reprocessing and deletion
belong to the ingestion phase. Ownership is enforced through owner-scoped repository reads; a
document or target collection owned by someone else is reported as not found (§9).
"""

from dataclasses import replace
from uuid import UUID

from doculens.application.auth import Clock
from doculens.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from doculens.domain.collections import CollectionNotFoundError
from doculens.domain.common import UNSET, Unset, clean_label
from doculens.domain.documents import MAX_FILENAME_LENGTH, Document, DocumentNotFoundError
from doculens.domain.errors import InvalidInputError
from doculens.domain.time import utc_now


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
    def __init__(self, *, unit_of_work: UnitOfWorkFactory, clock: Clock = utc_now) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    async def list_for_owner(
        self, owner_id: UUID, *, collection_id: UUID | None = None
    ) -> list[Document]:
        async with self._unit_of_work() as uow:
            if collection_id is None:
                return await uow.documents.list_for_owner(owner_id)
            await ensure_collection_owned(uow, owner_id, collection_id)
            return await uow.documents.list_in_collection(owner_id, collection_id)

    async def get(self, owner_id: UUID, document_id: UUID) -> Document:
        async with self._unit_of_work() as uow:
            document = await uow.documents.get(owner_id, document_id)
        if document is None:
            raise DocumentNotFoundError
        return document

    async def update(
        self,
        owner_id: UUID,
        document_id: UUID,
        *,
        filename: str | Unset = UNSET,
        collection_id: UUID | Unset | None = UNSET,
    ) -> Document:
        async with self._unit_of_work() as uow:
            current = await uow.documents.get(owner_id, document_id)
            if current is None:
                raise DocumentNotFoundError
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
            await uow.documents.update(updated)
            await uow.commit()
        return updated
