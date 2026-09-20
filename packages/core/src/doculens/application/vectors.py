"""Vector store port (SPECIFICATIONS.md §16, §18, §31, §32, §72).

Every operation is scoped to an owner: writes carry the owner in each record and are refused
when an identifier already belongs to someone else, searches always filter by owner, and deletes
only ever remove the owner's own vectors. A caller cannot express a cross-user operation.

Re-indexing (§32) is ``replace_document``: the new vectors are upserted under their stable ids
(the chunk ids) and vectors of the document that are not in the new set are removed, so an index
never holds duplicates or leftovers. Deleting a document (§31) removes every vector it owns.
"""

from collections.abc import Collection, Sequence
from typing import Protocol
from uuid import UUID

from doculens.domain.documents import DocumentChunk
from doculens.domain.embeddings import EmbeddingResult
from doculens.domain.vectors import (
    InvalidVectorRecordError,
    SearchFilter,
    SearchHit,
    VectorCollectionInfo,
    VectorMetadata,
    VectorRecord,
    vector_id_for,
)


class VectorStore(Protocol):
    @property
    def collection(self) -> str:
        """The index (collection) this store reads and writes."""
        ...

    async def upsert(self, owner_id: UUID, records: Sequence[VectorRecord]) -> None:
        """Insert or replace the owner's vectors by id; refuse ids owned by anyone else."""
        ...

    async def replace_document(
        self, owner_id: UUID, document_id: UUID, records: Sequence[VectorRecord]
    ) -> int:
        """Make ``records`` the document's complete vector set; returns how many were removed."""
        ...

    async def describe_document(
        self, owner_id: UUID, document_id: UUID
    ) -> dict[str, VectorMetadata]:
        """Metadata of every vector the owner's document currently has, by vector id."""
        ...

    async def prune_document(self, owner_id: UUID, document_id: UUID, keep: Collection[str]) -> int:
        """Remove the owner's vectors of the document whose ids are not in ``keep``."""
        ...

    async def set_document_collection(
        self, owner_id: UUID, document_id: UUID, collection_id: UUID | None
    ) -> int:
        """Refresh ``collection_id`` on the document's vectors after a move (§29, §16)."""
        ...

    async def delete(self, owner_id: UUID, vector_ids: Sequence[str]) -> int:
        """Remove the owner's vectors among ``vector_ids``; others are untouched. Idempotent."""
        ...

    async def delete_document(self, owner_id: UUID, document_id: UUID) -> int:
        """Remove every vector of the owner's document. Idempotent."""
        ...

    async def search(
        self, query: Sequence[float], *, scope: SearchFilter, limit: int
    ) -> list[SearchHit]:
        """Nearest vectors within the scope's owner, best first."""
        ...

    async def count(self, owner_id: UUID, document_id: UUID | None = None) -> int: ...

    async def ensure_collection(self) -> VectorCollectionInfo:
        """Create the collection if it does not exist and describe it."""
        ...

    async def drop_collection(self) -> None:
        """Remove the collection and every vector in it (rebuilds, tests)."""
        ...


def records_for_chunks(
    chunks: Sequence[DocumentChunk],
    embeddings: EmbeddingResult,
    *,
    owner_id: UUID,
    collection_id: UUID | None,
    embedding_provider: str,
) -> list[VectorRecord]:
    """Pair persisted chunks with their vectors; the two sequences must line up exactly."""
    if len(chunks) != len(embeddings.vectors):
        message = f"{len(chunks)} chunks but {len(embeddings.vectors)} vectors"
        raise InvalidVectorRecordError(message)
    records: list[VectorRecord] = []
    for chunk, vector in zip(chunks, embeddings.vectors, strict=True):
        page_number = chunk.metadata.get("page_number")
        content_hash = chunk.metadata.get("content_hash", "")
        metadata = VectorMetadata(
            user_id=owner_id,
            document_id=chunk.document_id,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            page_number=int(page_number) if isinstance(page_number, int) else 0,
            collection_id=collection_id,
            embedding_model=embeddings.model,
            embedding_provider=embedding_provider,
            content_hash=str(content_hash),
        )
        records.append(
            VectorRecord(
                id=vector_id_for(chunk.id), vector=vector, metadata=metadata, text=chunk.text
            )
        )
    return records


__all__ = ["VectorStore", "records_for_chunks"]
