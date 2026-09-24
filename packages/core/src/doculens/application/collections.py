"""Collection use cases (SPECIFICATIONS.md §7.2, §9, §30).

Every operation takes the acting user's ID and reaches the repository only through owner-scoped
methods, so a collection that belongs to someone else behaves exactly like one that does not
exist. Deleting a collection detaches its documents and conversations; it never deletes them
(§30, enforced by the schema). Vector metadata follows the detach so collection-scoped
retrieval does not keep pointing at a removed collection (§16).
"""

from dataclasses import replace
from uuid import UUID

from doculens.application.auth import Clock
from doculens.application.unit_of_work import UnitOfWorkFactory
from doculens.application.vectors import VectorStore
from doculens.domain.collections import (
    MAX_COLLECTION_DESCRIPTION_LENGTH,
    MAX_COLLECTION_NAME_LENGTH,
    Collection,
    CollectionNotFoundError,
)
from doculens.domain.common import UNSET, Unset, clean_label
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now


def _clean_description(description: str | None) -> str | None:
    if description is None:
        return None
    cleaned = description.strip()
    if not cleaned:
        return None
    return clean_label(cleaned, field="description", max_length=MAX_COLLECTION_DESCRIPTION_LENGTH)


class CollectionService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        vectors: VectorStore | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._vectors = vectors
        self._clock = clock

    async def create(self, owner_id: UUID, *, name: str, description: str | None) -> Collection:
        now = self._clock()
        collection = Collection(
            id=new_id(),
            owner_id=owner_id,
            name=clean_label(name, field="name", max_length=MAX_COLLECTION_NAME_LENGTH),
            description=_clean_description(description),
            created_at=now,
            updated_at=now,
        )
        async with self._unit_of_work() as uow:
            await uow.collections.add(collection)
            await uow.commit()
        return collection

    async def list_for_owner(self, owner_id: UUID) -> list[Collection]:
        async with self._unit_of_work() as uow:
            return await uow.collections.list_for_owner(owner_id)

    async def get(self, owner_id: UUID, collection_id: UUID) -> Collection:
        async with self._unit_of_work() as uow:
            collection = await uow.collections.get(owner_id, collection_id)
        if collection is None:
            raise CollectionNotFoundError
        return collection

    async def update(
        self,
        owner_id: UUID,
        collection_id: UUID,
        *,
        name: str | Unset = UNSET,
        description: str | Unset | None = UNSET,
    ) -> Collection:
        async with self._unit_of_work() as uow:
            current = await uow.collections.get(owner_id, collection_id)
            if current is None:
                raise CollectionNotFoundError
            updated = replace(
                current,
                name=(
                    current.name
                    if isinstance(name, Unset)
                    else clean_label(name, field="name", max_length=MAX_COLLECTION_NAME_LENGTH)
                ),
                description=(
                    current.description
                    if isinstance(description, Unset)
                    else _clean_description(description)
                ),
                updated_at=self._clock(),
            )
            await uow.collections.update(updated)
            await uow.commit()
        return updated

    async def delete(self, owner_id: UUID, collection_id: UUID) -> None:
        async with self._unit_of_work() as uow:
            if await uow.collections.get(owner_id, collection_id) is None:
                raise CollectionNotFoundError
            documents = await uow.documents.list_in_collection(owner_id, collection_id)
        # Clear vector collection_id before the relational detach so metadata never
        # references a collection that no longer exists.
        if self._vectors is not None:
            for document in documents:
                await self._vectors.set_document_collection(owner_id, document.id, None)
        async with self._unit_of_work() as uow:
            if not await uow.collections.delete(owner_id, collection_id):
                raise CollectionNotFoundError
            await uow.commit()
