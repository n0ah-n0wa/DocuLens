"""Behaviour every ``VectorStore`` must exhibit: insert, query, filters, delete, re-index,
duplicates and strict cross-user isolation (§16, §31, §32).

Test modules subclass :class:`VectorStoreContract` and provide a ``store`` fixture. The same
assertions run against the in-memory store (unit) and against ChromaDB (integration).
"""

from uuid import UUID, uuid4

import pytest

from doculens.application.vectors import VectorStore
from doculens.domain.chunking import chunk_id
from doculens.domain.vectors import (
    SearchFilter,
    VectorDimensionMismatchError,
    VectorMetadata,
    VectorOwnershipConflictError,
    VectorRecord,
    vector_id_for,
)

DIMENSIONS = 4


def axis(index: int, dimensions: int = DIMENSIONS) -> tuple[float, ...]:
    return tuple(1.0 if position == index else 0.0 for position in range(dimensions))


def record(  # noqa: PLR0913 - a test fixture builder mirrors the metadata fields
    owner: UUID,
    document: UUID,
    index: int,
    vector: tuple[float, ...],
    *,
    collection: UUID | None = None,
    page: int = 1,
    text: str = "",
) -> VectorRecord:
    chunk = chunk_id(document, index)
    return VectorRecord(
        id=vector_id_for(chunk),
        vector=vector,
        metadata=VectorMetadata(
            user_id=owner,
            document_id=document,
            chunk_id=chunk,
            chunk_index=index,
            page_number=page,
            collection_id=collection,
            embedding_model="fake-embedding-v1",
            embedding_provider="fake",
            content_hash=f"hash-{index}",
        ),
        text=text or f"chunk {index} of {document}",
    )


class VectorStoreContract:
    async def test_insert_then_query_returns_the_nearest_vectors_first(
        self, store: VectorStore
    ) -> None:
        owner, document = uuid4(), uuid4()
        await store.upsert(
            owner,
            [
                record(owner, document, 0, axis(0), page=1, text="north"),
                record(owner, document, 1, axis(1), page=2, text="east"),
                record(owner, document, 2, (0.9, 0.1, 0.0, 0.0), page=3, text="north-ish"),
            ],
        )

        hits = await store.search(axis(0), scope=SearchFilter(owner_id=owner), limit=2)

        assert [hit.metadata.chunk_index for hit in hits] == [0, 2]
        assert hits[0].score == pytest.approx(1.0)
        assert hits[0].score > hits[1].score > 0.5
        assert hits[0].text == "north"
        assert hits[0].page_number == 1
        assert hits[0].document_id == document
        assert hits[0].chunk_id == chunk_id(document, 0)
        assert hits[0].metadata.embedding_model == "fake-embedding-v1"
        assert await store.count(owner) == 3
        assert await store.count(owner, document) == 3

    async def test_metadata_filters_narrow_within_the_owner_scope(self, store: VectorStore) -> None:
        owner = uuid4()
        first, second, third = uuid4(), uuid4(), uuid4()
        shared_collection = uuid4()
        await store.upsert(
            owner,
            [
                record(owner, first, 0, axis(0), collection=shared_collection),
                record(owner, second, 0, (0.8, 0.2, 0.0, 0.0), collection=shared_collection),
                record(owner, third, 0, (0.7, 0.3, 0.0, 0.0)),
            ],
        )

        by_document = await store.search(
            axis(0), scope=SearchFilter(owner_id=owner, document_ids=(second, third)), limit=10
        )
        by_collection = await store.search(
            axis(0), scope=SearchFilter(owner_id=owner, collection_id=shared_collection), limit=10
        )
        both = await store.search(
            axis(0),
            scope=SearchFilter(
                owner_id=owner, document_ids=(first,), collection_id=shared_collection
            ),
            limit=10,
        )
        none = await store.search(
            axis(0), scope=SearchFilter(owner_id=owner, document_ids=(uuid4(),)), limit=10
        )

        assert {hit.document_id for hit in by_document} == {second, third}
        assert {hit.document_id for hit in by_collection} == {first, second}
        assert [hit.document_id for hit in both] == [first]
        assert none == []

    async def test_limit_is_respected(self, store: VectorStore) -> None:
        owner, document = uuid4(), uuid4()
        await store.upsert(
            owner, [record(owner, document, i, (1.0, float(i) / 10, 0.0, 0.0)) for i in range(6)]
        )

        hits = await store.search(axis(0), scope=SearchFilter(owner_id=owner), limit=3)

        assert len(hits) == 3

    async def test_delete_by_id_and_by_document_is_idempotent(self, store: VectorStore) -> None:
        owner, document, other = uuid4(), uuid4(), uuid4()
        records = [record(owner, document, i, axis(i)) for i in range(3)]
        await store.upsert(owner, [*records, record(owner, other, 0, axis(3))])

        assert await store.delete(owner, [records[0].id]) == 1
        assert await store.delete(owner, [records[0].id, "not-a-vector"]) == 0
        assert await store.count(owner, document) == 2
        assert await store.delete_document(owner, document) == 2
        assert await store.delete_document(owner, document) == 0
        assert await store.count(owner, document) == 0
        assert await store.count(owner, other) == 1
        hits = await store.search(axis(0), scope=SearchFilter(owner_id=owner), limit=10)
        assert [hit.document_id for hit in hits] == [other]

    async def test_upserting_the_same_id_replaces_without_duplicating(
        self, store: VectorStore
    ) -> None:
        owner, document = uuid4(), uuid4()
        await store.upsert(owner, [record(owner, document, 0, axis(0), text="v1")])

        await store.upsert(owner, [record(owner, document, 0, axis(1), page=7, text="v2")])

        assert await store.count(owner, document) == 1
        (hit,) = await store.search(axis(1), scope=SearchFilter(owner_id=owner), limit=10)
        assert hit.score == pytest.approx(1.0)
        assert hit.text == "v2"
        assert hit.page_number == 7

    async def test_reindexing_keeps_stable_ids_and_removes_stale_vectors(
        self, store: VectorStore
    ) -> None:
        owner, document, untouched = uuid4(), uuid4(), uuid4()
        await store.upsert(owner, [record(owner, document, i, axis(i)) for i in range(4)])
        await store.upsert(owner, [record(owner, untouched, 0, axis(0))])

        removed = await store.replace_document(
            owner, document, [record(owner, document, i, axis(i), text="new") for i in range(2)]
        )
        again = await store.replace_document(
            owner, document, [record(owner, document, i, axis(i), text="new") for i in range(2)]
        )

        assert removed == 2
        assert again == 0
        assert await store.count(owner, document) == 2
        assert await store.count(owner, untouched) == 1
        hits = await store.search(
            axis(0), scope=SearchFilter(owner_id=owner, document_ids=(document,)), limit=10
        )
        assert {hit.id for hit in hits} == {vector_id_for(chunk_id(document, i)) for i in range(2)}
        assert all(hit.text == "new" for hit in hits)

    async def test_users_never_see_touch_or_overwrite_each_others_vectors(
        self, store: VectorStore
    ) -> None:
        alice, bob = uuid4(), uuid4()
        shared_document = uuid4()  # same document id under two users must still isolate
        await store.upsert(alice, [record(alice, shared_document, 0, axis(0), text="alice")])

        # Bob cannot read Alice's vector, however he filters.
        assert await store.search(axis(0), scope=SearchFilter(owner_id=bob), limit=10) == []
        assert (
            await store.search(
                axis(0),
                scope=SearchFilter(owner_id=bob, document_ids=(shared_document,)),
                limit=10,
            )
            == []
        )
        assert await store.count(bob) == 0
        assert await store.count(bob, shared_document) == 0

        # Bob cannot delete it, by id or by document.
        alice_id = vector_id_for(chunk_id(shared_document, 0))
        assert await store.delete(bob, [alice_id]) == 0
        assert await store.delete_document(bob, shared_document) == 0
        assert await store.count(alice, shared_document) == 1

        # Bob cannot overwrite it, even with a record claiming to be his.
        with pytest.raises(VectorOwnershipConflictError):
            await store.upsert(bob, [record(bob, shared_document, 0, axis(1), text="bob")])
        with pytest.raises(VectorOwnershipConflictError):
            await store.replace_document(
                bob, shared_document, [record(bob, shared_document, 0, axis(1), text="bob")]
            )
        (hit,) = await store.search(axis(0), scope=SearchFilter(owner_id=alice), limit=10)
        assert hit.text == "alice"

        # A record whose metadata names another owner is refused outright.
        with pytest.raises(VectorOwnershipConflictError):
            await store.upsert(bob, [record(alice, uuid4(), 0, axis(2))])

    async def test_vectors_of_the_wrong_dimension_are_refused(self, store: VectorStore) -> None:
        owner, document = uuid4(), uuid4()
        await store.upsert(owner, [record(owner, document, 0, axis(0))])

        with pytest.raises(VectorDimensionMismatchError):
            await store.upsert(owner, [record(owner, document, 1, (1.0, 0.0))])
        with pytest.raises(VectorDimensionMismatchError):
            await store.search((1.0, 0.0), scope=SearchFilter(owner_id=owner), limit=1)

    async def test_the_collection_can_be_described_and_dropped(self, store: VectorStore) -> None:
        owner, document = uuid4(), uuid4()
        await store.upsert(owner, [record(owner, document, 0, axis(0))])

        info = await store.ensure_collection()
        assert info.name == store.collection
        assert info.count == 1

        await store.drop_collection()

        assert (await store.ensure_collection()).count == 0
        assert await store.count(owner) == 0

    async def test_describe_prune_and_collection_moves(self, store: VectorStore) -> None:
        owner, document, other = uuid4(), uuid4(), uuid4()
        source, target = uuid4(), uuid4()
        await store.upsert(
            owner,
            [
                record(owner, document, 0, axis(0), collection=source, text="a"),
                record(owner, document, 1, axis(1), collection=source, text="b"),
                record(owner, other, 0, axis(2), collection=source),
            ],
        )

        described = await store.describe_document(owner, document)
        assert set(described) == {vector_id_for(chunk_id(document, i)) for i in range(2)}
        assert all(m.collection_id == source for m in described.values())
        assert described[vector_id_for(chunk_id(document, 1))].content_hash == "hash-1"
        assert await store.describe_document(uuid4(), document) == {}

        moved = await store.set_document_collection(owner, document, target)
        assert moved == 2
        after = await store.describe_document(owner, document)
        assert all(m.collection_id == target for m in after.values())
        assert (await store.describe_document(owner, other))[
            vector_id_for(chunk_id(other, 0))
        ].collection_id == source
        hits = await store.search(
            axis(0), scope=SearchFilter(owner_id=owner, collection_id=target), limit=5
        )
        assert [hit.text for hit in hits] == ["a", "b"]
        assert await store.set_document_collection(owner, document, None) == 2
        assert all(
            m.collection_id is None
            for m in (await store.describe_document(owner, document)).values()
        )
        assert await store.set_document_collection(uuid4(), document, target) == 0

        pruned = await store.prune_document(
            owner, document, keep={vector_id_for(chunk_id(document, 0))}
        )
        assert pruned == 1
        assert set(await store.describe_document(owner, document)) == {
            vector_id_for(chunk_id(document, 0))
        }
        assert await store.prune_document(owner, document, keep=[]) == 1
        assert await store.count(owner, document) == 0
        assert await store.count(owner, other) == 1
