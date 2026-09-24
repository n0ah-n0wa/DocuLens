"""Deleting a collection clears ``collection_id`` on detached document vectors (§16, §30)."""

from uuid import UUID

import pytest

from doculens.application.collections import CollectionService
from doculens.domain.chunking import chunk_id
from doculens.domain.vectors import (
    VectorMetadata,
    VectorRecord,
    VectorStoreUnavailableError,
    vector_id_for,
)
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit


async def test_deleting_a_collection_clears_vector_collection_ids() -> None:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    collection = Factories.collection(owner.id, "Contracts")
    store.collections[collection.id] = collection
    document = Factories.document(owner.id, collection.id)
    store.documents[document.id] = document
    vectors = InMemoryVectorStore()
    chunk = chunk_id(document.id, 0)
    await vectors.upsert(
        owner.id,
        [
            VectorRecord(
                id=vector_id_for(chunk),
                vector=(1.0, 0.0),
                metadata=VectorMetadata(
                    user_id=owner.id,
                    document_id=document.id,
                    chunk_id=chunk,
                    chunk_index=0,
                    page_number=1,
                    collection_id=collection.id,
                ),
            )
        ],
    )
    service = CollectionService(unit_of_work=lambda: InMemoryUnitOfWork(store), vectors=vectors)

    await service.delete(owner.id, collection.id)

    assert collection.id not in store.collections
    assert store.documents[document.id].collection_id is None
    (metadata,) = (await vectors.describe_document(owner.id, document.id)).values()
    assert metadata.collection_id is None


async def test_collection_delete_aborts_when_vector_detach_fails() -> None:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    collection = Factories.collection(owner.id, "Contracts")
    store.collections[collection.id] = collection
    document = Factories.document(owner.id, collection.id)
    store.documents[document.id] = document

    class FailingVectors(InMemoryVectorStore):
        async def set_document_collection(
            self, owner_id: UUID, document_id: UUID, collection_id: UUID | None
        ) -> int:
            del owner_id, document_id, collection_id
            raise VectorStoreUnavailableError

    service = CollectionService(
        unit_of_work=lambda: InMemoryUnitOfWork(store), vectors=FailingVectors()
    )

    with pytest.raises(VectorStoreUnavailableError):
        await service.delete(owner.id, collection.id)

    assert collection.id in store.collections
    assert store.documents[document.id].collection_id == collection.id
