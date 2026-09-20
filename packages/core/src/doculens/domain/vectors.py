"""Vector-store concepts (SPECIFICATIONS.md §16, §18, §32).

A vector is identified by the chunk it embeds, so re-indexing overwrites instead of duplicating
(§32), and it carries the metadata §16 requires for filtering and, above all, for ownership:
every query and every deletion is scoped to one ``user_id``. Nothing here knows ChromaDB.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self
from uuid import UUID

from doculens.domain.embeddings import Vector
from doculens.domain.errors import DependencyUnavailableError, DomainError, InvalidInputError

MetadataValue = str | int | float | bool
_COLLECTION_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,510}[a-z0-9]$")


class VectorStoreError(DomainError):
    code = "VECTOR_STORE_ERROR"
    default_message = "The vector store request could not be completed."

    def __init__(self, message: str | None = None, *, diagnostics: str | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class VectorStoreUnavailableError(DependencyUnavailableError):
    code = "VECTOR_STORE_UNAVAILABLE"
    default_message = "The vector store is temporarily unavailable."


class VectorDimensionMismatchError(VectorStoreError):
    """The vectors do not match the dimension the collection was created with (§32)."""

    code = "VECTOR_DIMENSION_MISMATCH"
    default_message = "The vectors do not match the index dimension."


class VectorOwnershipConflictError(VectorStoreError):
    """A write would touch a vector that belongs to another user; refused, never merged."""

    code = "VECTOR_OWNERSHIP_CONFLICT"
    default_message = "The vector identifiers are not available to this user."


class InvalidVectorRecordError(InvalidInputError):
    code = "INVALID_VECTOR_RECORD"
    default_message = "The vector record is not valid."


def vector_id_for(chunk_id: UUID) -> str:
    """The vector's identity is the chunk's identity (§32)."""
    return str(chunk_id)


def collection_name(prefix: str, embedding_model: str) -> str:
    """A collection per embedding model: a changed model lands in a fresh index (§32)."""
    slug = re.sub(r"[^a-z0-9]+", "-", embedding_model.lower()).strip("-")
    if not slug:
        message = "the embedding model name has no usable characters"
        raise InvalidVectorRecordError(message)
    name = f"{prefix}-{slug}"
    if _COLLECTION_NAME_PATTERN.fullmatch(name) is None:
        message = f"collection name {name!r} is not valid"
        raise InvalidVectorRecordError(message)
    return name


@dataclass(frozen=True, slots=True)
class VectorMetadata:
    """The §16 filter fields plus what staleness detection needs (§32)."""

    user_id: UUID
    document_id: UUID
    chunk_id: UUID
    chunk_index: int
    page_number: int
    collection_id: UUID | None = None
    embedding_model: str = ""
    embedding_provider: str = ""
    content_hash: str = ""

    def as_mapping(self) -> dict[str, MetadataValue]:
        """Flat scalar mapping, as vector stores require: never null, and ``collection_id`` is
        always present (an empty string means "no collection") because stores merge metadata
        on update and an omitted key could not clear a previous value."""
        mapping: dict[str, MetadataValue] = {
            "user_id": str(self.user_id),
            "document_id": str(self.document_id),
            "chunk_id": str(self.chunk_id),
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "collection_id": str(self.collection_id) if self.collection_id is not None else "",
        }
        if self.embedding_model:
            mapping["embedding_model"] = self.embedding_model
        if self.embedding_provider:
            mapping["embedding_provider"] = self.embedding_provider
        if self.content_hash:
            mapping["content_hash"] = self.content_hash
        return mapping

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> Self:
        try:
            collection = mapping.get("collection_id")
            return cls(
                user_id=UUID(str(mapping["user_id"])),
                document_id=UUID(str(mapping["document_id"])),
                chunk_id=UUID(str(mapping["chunk_id"])),
                chunk_index=int(str(mapping["chunk_index"])),
                page_number=int(str(mapping["page_number"])),
                collection_id=UUID(str(collection)) if collection else None,
                embedding_model=str(mapping.get("embedding_model", "")),
                embedding_provider=str(mapping.get("embedding_provider", "")),
                content_hash=str(mapping.get("content_hash", "")),
            )
        except (KeyError, ValueError, TypeError) as exc:
            message = f"vector metadata is incomplete or malformed: {exc}"
            raise InvalidVectorRecordError(message) from exc


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """One chunk's vector with its text (for context assembly) and its metadata."""

    id: str
    vector: Vector
    metadata: VectorMetadata
    text: str = ""

    def __post_init__(self) -> None:
        if self.id != vector_id_for(self.metadata.chunk_id):
            message = "a vector id must be its chunk id"
            raise InvalidVectorRecordError(message)
        if not self.vector or any(not math.isfinite(value) for value in self.vector):
            message = "a vector must be non-empty and finite"
            raise InvalidVectorRecordError(message)


@dataclass(frozen=True, slots=True)
class SearchFilter:
    """Every search is scoped to an owner (§16); the rest narrows within that scope."""

    owner_id: UUID
    document_ids: tuple[UUID, ...] | None = None
    collection_id: UUID | None = None

    def matches(self, metadata: VectorMetadata) -> bool:
        if metadata.user_id != self.owner_id:
            return False
        if self.document_ids is not None and metadata.document_id not in self.document_ids:
            return False
        return self.collection_id is None or metadata.collection_id == self.collection_id


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A retrieved vector; ``score`` is cosine similarity in ``[-1, 1]``, higher is closer."""

    id: str
    score: float
    metadata: VectorMetadata
    text: str = ""

    @property
    def chunk_id(self) -> UUID:
        return self.metadata.chunk_id

    @property
    def document_id(self) -> UUID:
        return self.metadata.document_id

    @property
    def page_number(self) -> int:
        return self.metadata.page_number


@dataclass(frozen=True, slots=True)
class VectorCollectionInfo:
    name: str
    count: int
    metadata: Mapping[str, object] = field(default_factory=dict)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        message = f"vector dimensions differ: {len(left)} and {len(right)}"
        raise VectorDimensionMismatchError(diagnostics=message)
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = [
    "InvalidVectorRecordError",
    "MetadataValue",
    "SearchFilter",
    "SearchHit",
    "VectorCollectionInfo",
    "VectorDimensionMismatchError",
    "VectorMetadata",
    "VectorOwnershipConflictError",
    "VectorRecord",
    "VectorStoreError",
    "VectorStoreUnavailableError",
    "collection_name",
    "cosine_similarity",
    "vector_id_for",
]
