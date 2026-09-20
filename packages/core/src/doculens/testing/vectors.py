"""In-memory ``VectorStore`` with the same ownership rules as the ChromaDB adapter.

Brute-force cosine search over a dictionary; used by unit tests of use cases and as the second
implementation the contract suite runs against so the adapter cannot drift.
"""

from collections.abc import Collection, Sequence
from dataclasses import replace
from uuid import UUID

from doculens.domain.vectors import (
    SearchFilter,
    SearchHit,
    VectorCollectionInfo,
    VectorDimensionMismatchError,
    VectorMetadata,
    VectorOwnershipConflictError,
    VectorRecord,
    cosine_similarity,
)


class InMemoryVectorStore:
    def __init__(self, collection: str = "doculens-fake", *, max_results: int = 100) -> None:
        self._collection = collection
        self._max_results = max_results
        self._records: dict[str, VectorRecord] = {}
        self._dimensions: int | None = None

    @property
    def collection(self) -> str:
        return self._collection

    @property
    def records(self) -> dict[str, VectorRecord]:
        return self._records

    async def upsert(self, owner_id: UUID, records: Sequence[VectorRecord]) -> None:
        for record in records:
            if record.metadata.user_id != owner_id:
                message = "record owner differs from the caller"
                raise VectorOwnershipConflictError(diagnostics=message)
            existing = self._records.get(record.id)
            if existing is not None and existing.metadata.user_id != owner_id:
                raise VectorOwnershipConflictError
            if self._dimensions is None:
                self._dimensions = len(record.vector)
            elif len(record.vector) != self._dimensions:
                message = f"expected {self._dimensions} dimensions, got {len(record.vector)}"
                raise VectorDimensionMismatchError(diagnostics=message)
        for record in records:
            self._records[record.id] = record

    async def replace_document(
        self, owner_id: UUID, document_id: UUID, records: Sequence[VectorRecord]
    ) -> int:
        for record in records:
            if record.metadata.document_id != document_id:
                message = "record belongs to another document"
                raise VectorOwnershipConflictError(diagnostics=message)
        await self.upsert(owner_id, records)
        return await self.prune_document(owner_id, document_id, {r.id for r in records})

    async def describe_document(
        self, owner_id: UUID, document_id: UUID
    ) -> dict[str, VectorMetadata]:
        return {
            vector_id: record.metadata
            for vector_id, record in self._records.items()
            if record.metadata.user_id == owner_id and record.metadata.document_id == document_id
        }

    async def prune_document(self, owner_id: UUID, document_id: UUID, keep: Collection[str]) -> int:
        stale = [
            vector_id
            for vector_id in await self.describe_document(owner_id, document_id)
            if vector_id not in keep
        ]
        for vector_id in stale:
            del self._records[vector_id]
        return len(stale)

    async def set_document_collection(
        self, owner_id: UUID, document_id: UUID, collection_id: UUID | None
    ) -> int:
        ids = list(await self.describe_document(owner_id, document_id))
        for vector_id in ids:
            record = self._records[vector_id]
            self._records[vector_id] = replace(
                record, metadata=replace(record.metadata, collection_id=collection_id)
            )
        return len(ids)

    async def delete(self, owner_id: UUID, vector_ids: Sequence[str]) -> int:
        removed = 0
        for vector_id in vector_ids:
            record = self._records.get(vector_id)
            if record is not None and record.metadata.user_id == owner_id:
                del self._records[vector_id]
                removed += 1
        return removed

    async def delete_document(self, owner_id: UUID, document_id: UUID) -> int:
        ids = [
            vector_id
            for vector_id, record in self._records.items()
            if record.metadata.user_id == owner_id and record.metadata.document_id == document_id
        ]
        return await self.delete(owner_id, ids)

    async def search(
        self, query: Sequence[float], *, scope: SearchFilter, limit: int
    ) -> list[SearchHit]:
        if self._dimensions is not None and len(query) != self._dimensions:
            message = f"expected {self._dimensions} dimensions, got {len(query)}"
            raise VectorDimensionMismatchError(diagnostics=message)
        hits = [
            SearchHit(
                id=record.id,
                score=cosine_similarity(query, record.vector),
                metadata=record.metadata,
                text=record.text,
            )
            for record in self._records.values()
            if scope.matches(record.metadata)
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.id))
        return hits[: max(0, min(limit, self._max_results))]

    async def count(self, owner_id: UUID, document_id: UUID | None = None) -> int:
        return sum(
            1
            for record in self._records.values()
            if record.metadata.user_id == owner_id
            and (document_id is None or record.metadata.document_id == document_id)
        )

    async def ensure_collection(self) -> VectorCollectionInfo:
        return VectorCollectionInfo(name=self._collection, count=len(self._records))

    async def drop_collection(self) -> None:
        self._records.clear()
        self._dimensions = None

    async def check(self) -> None:
        return None
