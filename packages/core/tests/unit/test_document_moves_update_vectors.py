"""Moving a document between collections keeps the vectors' collection metadata true (§16, §29)."""

from uuid import UUID, uuid4

import pytest

from doculens.application.documents import DocumentService
from doculens.domain.chunking import chunk_id
from doculens.domain.vectors import (
    SearchFilter,
    VectorMetadata,
    VectorRecord,
    VectorStoreUnavailableError,
    vector_id_for,
)
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit


class _FailingCollectionVectors(InMemoryVectorStore):
    async def set_document_collection(
        self, owner_id: UUID, document_id: UUID, collection_id: UUID | None
    ) -> int:
        del owner_id, document_id, collection_id
        raise VectorStoreUnavailableError


async def test_a_collection_move_rewrites_the_document_vectors() -> None:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    source = Factories.collection(owner.id, "Source")
    target = Factories.collection(owner.id, "Target")
    store.collections[source.id] = source
    store.collections[target.id] = target
    document = Factories.document(owner.id, source.id)
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
                    collection_id=source.id,
                ),
            )
        ],
    )
    service = DocumentService(unit_of_work=lambda: InMemoryUnitOfWork(store), vectors=vectors)

    moved = await service.update(owner.id, document.id, collection_id=target.id)
    assert moved.collection_id == target.id
    hits = await vectors.search(
        (1.0, 0.0), scope=SearchFilter(owner_id=owner.id, collection_id=target.id), limit=5
    )
    assert [hit.document_id for hit in hits] == [document.id]
    assert (
        await vectors.search(
            (1.0, 0.0), scope=SearchFilter(owner_id=owner.id, collection_id=source.id), limit=5
        )
        == []
    )

    detached = await service.update(owner.id, document.id, collection_id=None)
    assert detached.collection_id is None
    (metadata,) = (await vectors.describe_document(owner.id, document.id)).values()
    assert metadata.collection_id is None

    # A rename does not touch the vectors at all.
    renamed = await service.update(owner.id, document.id, filename="renamed.pdf")
    assert renamed.filename == "renamed.pdf"
    assert renamed.collection_id is None
    assert (await vectors.describe_document(owner.id, uuid4())) == {}


async def test_a_failed_vector_move_reverts_the_postgres_collection() -> None:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    source = Factories.collection(owner.id, "Source")
    target = Factories.collection(owner.id, "Target")
    store.collections[source.id] = source
    store.collections[target.id] = target
    document = Factories.document(owner.id, source.id)
    store.documents[document.id] = document
    service = DocumentService(
        unit_of_work=lambda: InMemoryUnitOfWork(store), vectors=_FailingCollectionVectors()
    )

    with pytest.raises(VectorStoreUnavailableError):
        await service.update(owner.id, document.id, collection_id=target.id)

    assert store.documents[document.id].collection_id == source.id
