"""ChromaDB adapter for the ``VectorStore`` port (SPECIFICATIONS.md §16, §18, §31, §32, §67).

Tenant isolation is enforced by the adapter, not by callers: every ``where`` clause starts with
the owner's ``user_id``, every delete carries it, results are re-checked against it before they
are returned, and an upsert first verifies that none of its ids already belongs to another user.

One Chroma collection holds the vectors of one embedding model (``collection_name``), so a model
change starts a fresh index instead of mixing dimensions; the collection uses cosine space and
the adapter reports cosine similarity (``1 - distance``) as the score.
"""

import asyncio
import logging
from collections.abc import Callable, Collection, Coroutine, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import chromadb
import httpx
from chromadb.api.async_api import AsyncClientAPI
from chromadb.api.models.AsyncCollection import AsyncCollection
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import ChromaError, InvalidArgumentError, NotFoundError

from doculens.domain.vectors import (
    InvalidVectorRecordError,
    SearchFilter,
    SearchHit,
    VectorCollectionInfo,
    VectorDimensionMismatchError,
    VectorMetadata,
    VectorOwnershipConflictError,
    VectorRecord,
    VectorStoreError,
    VectorStoreUnavailableError,
)

logger = logging.getLogger(__name__)

PROBE_NAME = "vector-store"
DEFAULT_BATCH_SIZE = 500
_MAX_DIAGNOSTIC_CHARACTERS = 300


class ChromaVectorStore:
    name = PROBE_NAME

    def __init__(
        self,
        client_factory: Callable[[], Coroutine[Any, Any, AsyncClientAPI]],
        *,
        collection: str,
        collection_metadata: Mapping[str, str] | None = None,
        max_results: int = 100,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client_factory = client_factory
        self._collection_name = collection
        self._collection_metadata = dict(collection_metadata or {})
        self._max_results = max_results
        self._batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._client: AsyncClientAPI | None = None
        self._collection: AsyncCollection | None = None

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        collection: str,
        api_token: str | None = None,
        timeout_seconds: float = 10.0,
        collection_metadata: Mapping[str, str] | None = None,
        max_results: int = 100,
    ) -> "ChromaVectorStore":
        parsed = httpx.URL(url)
        ssl = parsed.scheme == "https"
        headers = {"X-Chroma-Token": api_token} if api_token else None
        settings = ChromaSettings(anonymized_telemetry=False)

        async def connect() -> AsyncClientAPI:
            return await chromadb.AsyncHttpClient(
                host=parsed.host,
                port=parsed.port or (443 if ssl else 80),
                ssl=ssl,
                headers=headers,
                settings=settings,
            )

        return cls(
            connect,
            collection=collection,
            collection_metadata=collection_metadata,
            max_results=max_results,
            timeout_seconds=timeout_seconds,
        )

    @property
    def collection(self) -> str:
        return self._collection_name

    # -- port ------------------------------------------------------------------------------------

    async def upsert(self, owner_id: UUID, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        _ensure_owned(owner_id, records)
        collection = await self._ready()
        await self._assert_ids_available(collection, owner_id, [record.id for record in records])
        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            await self._call(
                collection.upsert(
                    ids=[record.id for record in batch],
                    embeddings=cast("Any", [list(record.vector) for record in batch]),
                    metadatas=[cast("Any", record.metadata.as_mapping()) for record in batch],
                    documents=[record.text for record in batch],
                )
            )
        logger.info(
            "vectors upserted",
            extra={
                "operation": "vectors.upsert",
                "collection": self._collection_name,
                "user_id": str(owner_id),
                "count": len(records),
            },
        )

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
        collection = await self._ready()
        where = _where_clause(SearchFilter(owner_id=owner_id, document_ids=(document_id,)))
        found = await self._call(collection.get(where=where, include=cast("Any", ["metadatas"])))
        metadatas = found.get("metadatas") or []
        described: dict[str, VectorMetadata] = {}
        for position, vector_id in enumerate(found["ids"]):
            raw = metadatas[position] if position < len(metadatas) else None
            described[str(vector_id)] = VectorMetadata.from_mapping(dict(raw or {}))
        return described

    async def prune_document(self, owner_id: UUID, document_id: UUID, keep: Collection[str]) -> int:
        collection = await self._ready()
        existing = await self._owned_ids(collection, owner_id, document_id)
        stale = sorted(existing - set(keep))
        for start in range(0, len(stale), self._batch_size):
            batch = stale[start : start + self._batch_size]
            await self._call(collection.delete(ids=batch, where=_owner_clause(owner_id)))
        return len(stale)

    async def set_document_collection(
        self, owner_id: UUID, document_id: UUID, collection_id: UUID | None
    ) -> int:
        """Rewrite the document's vectors with the new collection (an upsert keeps every field)."""
        collection = await self._ready()
        ids = sorted(await self._owned_ids(collection, owner_id, document_id))
        for start in range(0, len(ids), self._batch_size):
            batch = ids[start : start + self._batch_size]
            found = await self._call(
                collection.get(
                    ids=batch,
                    where=_owner_clause(owner_id),
                    include=cast("Any", ["embeddings", "metadatas", "documents"]),
                )
            )
            embeddings = found.get("embeddings")
            metadatas = found.get("metadatas") or []
            documents = found.get("documents") or []
            if embeddings is None:
                continue
            records = [
                VectorRecord(
                    id=str(vector_id),
                    vector=tuple(float(value) for value in embeddings[position]),
                    metadata=replace(
                        VectorMetadata.from_mapping(dict(metadatas[position] or {})),
                        collection_id=collection_id,
                    ),
                    text=str(documents[position] or "") if documents else "",
                )
                for position, vector_id in enumerate(found["ids"])
            ]
            if records:
                await self._call(
                    collection.upsert(
                        ids=[record.id for record in records],
                        embeddings=cast("Any", [list(record.vector) for record in records]),
                        metadatas=[cast("Any", r.metadata.as_mapping()) for r in records],
                        documents=[record.text for record in records],
                    )
                )
        return len(ids)

    async def delete(self, owner_id: UUID, vector_ids: Sequence[str]) -> int:
        if not vector_ids:
            return 0
        collection = await self._ready()
        owned = await self._call(
            collection.get(ids=list(vector_ids), where=_owner_clause(owner_id), include=[])
        )
        ids = list(owned["ids"])
        if ids:
            await self._call(collection.delete(ids=ids, where=_owner_clause(owner_id)))
        return len(ids)

    async def delete_document(self, owner_id: UUID, document_id: UUID) -> int:
        collection = await self._ready()
        ids = sorted(await self._owned_ids(collection, owner_id, document_id))
        if ids:
            await self._call(collection.delete(ids=ids, where=_owner_clause(owner_id)))
            logger.info(
                "document vectors deleted",
                extra={
                    "operation": "vectors.delete_document",
                    "collection": self._collection_name,
                    "document_id": str(document_id),
                    "count": len(ids),
                },
            )
        return len(ids)

    async def search(
        self, query: Sequence[float], *, scope: SearchFilter, limit: int
    ) -> list[SearchHit]:
        limit = max(0, min(limit, self._max_results))
        if limit == 0:
            return []
        collection = await self._ready()
        response = await self._call(
            collection.query(
                query_embeddings=cast("Any", [list(query)]),
                n_results=limit,
                where=_where_clause(scope),
                include=cast("Any", ["metadatas", "documents", "distances"]),
            )
        )
        ids = response["ids"][0] if response["ids"] else []
        metadatas = (response.get("metadatas") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        hits: list[SearchHit] = []
        for position, vector_id in enumerate(ids):
            metadata = VectorMetadata.from_mapping(dict(metadatas[position] or {}))
            if not scope.matches(metadata):
                # The where clause already excluded this; a mismatch here is a store defect.
                logger.error(
                    "vector store returned a vector outside the owner scope",
                    extra={"operation": "vectors.isolation", "vector_id": vector_id},
                )
                continue
            distance = float(distances[position]) if distances else 0.0
            text = documents[position] if documents else None
            hits.append(
                SearchHit(
                    id=str(vector_id),
                    score=1.0 - distance,
                    metadata=metadata,
                    text=str(text) if text is not None else "",
                )
            )
        return hits

    async def count(self, owner_id: UUID, document_id: UUID | None = None) -> int:
        collection = await self._ready()
        where = _where_clause(
            SearchFilter(owner_id=owner_id, document_ids=(document_id,) if document_id else None)
        )
        found = await self._call(collection.get(where=where, include=[]))
        return len(found["ids"])

    async def ensure_collection(self) -> VectorCollectionInfo:
        collection = await self._ready()
        total = await self._call(collection.count())
        return VectorCollectionInfo(
            name=self._collection_name, count=total, metadata=dict(collection.metadata or {})
        )

    async def drop_collection(self) -> None:
        client = await self._connect()
        try:
            await asyncio.wait_for(
                client.delete_collection(self._collection_name), timeout=self._timeout_seconds
            )
        except NotFoundError:
            pass
        except TimeoutError as exc:
            raise VectorStoreUnavailableError from exc
        except (httpx.HTTPError, OSError) as exc:
            raise VectorStoreUnavailableError from exc
        except ChromaError as exc:
            raise VectorStoreError(diagnostics=str(exc)[:_MAX_DIAGNOSTIC_CHARACTERS]) from exc
        self._collection = None

    async def check(self) -> None:
        """Readiness: the server answers and the collection can be opened."""
        client = await self._connect()
        await self._call(client.heartbeat())
        await self._ready()

    # -- helpers ---------------------------------------------------------------------------------

    async def _connect(self) -> AsyncClientAPI:
        if self._client is None:
            try:
                self._client = await self._client_factory()
            except (httpx.HTTPError, ChromaError, OSError) as exc:
                raise VectorStoreUnavailableError from exc
        return self._client

    async def _ready(self) -> AsyncCollection:
        if self._collection is None:
            client = await self._connect()
            self._collection = await self._call(
                client.get_or_create_collection(
                    self._collection_name,
                    configuration=cast("Any", {"hnsw": {"space": "cosine"}}),
                    metadata=cast("Any", self._collection_metadata or None),
                )
            )
        return self._collection

    async def _owned_ids(
        self, collection: AsyncCollection, owner_id: UUID, document_id: UUID
    ) -> set[str]:
        where = _where_clause(SearchFilter(owner_id=owner_id, document_ids=(document_id,)))
        found = await self._call(collection.get(where=where, include=[]))
        return set(found["ids"])

    async def _assert_ids_available(
        self, collection: AsyncCollection, owner_id: UUID, ids: list[str]
    ) -> None:
        """Refuse an upsert that would overwrite a vector owned by another user."""
        foreign = await self._call(
            collection.get(
                ids=ids, where=cast("Any", {"user_id": {"$ne": str(owner_id)}}), include=[]
            )
        )
        if foreign["ids"]:
            logger.warning(
                "upsert refused: ids belong to another user",
                extra={
                    "operation": "vectors.ownership_conflict",
                    "user_id": str(owner_id),
                    "count": len(foreign["ids"]),
                },
            )
            raise VectorOwnershipConflictError

    async def _call[T](self, operation: Coroutine[Any, Any, T]) -> T:
        try:
            return await asyncio.wait_for(operation, timeout=self._timeout_seconds)
        except TimeoutError as exc:
            logger.warning(
                "vector store request timed out",
                extra={"operation": "vectors.timeout", "timeout": self._timeout_seconds},
            )
            raise VectorStoreUnavailableError from exc
        except InvalidArgumentError as exc:
            detail = str(exc)[:_MAX_DIAGNOSTIC_CHARACTERS]
            if "dimension" in detail.lower():
                raise VectorDimensionMismatchError(diagnostics=detail) from exc
            raise VectorStoreError(diagnostics=detail) from exc
        except NotFoundError as exc:
            # The collection was dropped or rebuilt behind us: forget the handle so the next
            # attempt recreates it, and let the job retry (§32 "vector store was rebuilt").
            self._collection = None
            logger.warning(
                "vector store collection is gone; it will be recreated on retry",
                extra={
                    "operation": "vectors.collection_missing",
                    "collection": self._collection_name,
                },
            )
            raise VectorStoreUnavailableError from exc
        except (httpx.HTTPError, OSError) as exc:
            logger.warning(
                "vector store unreachable",
                extra={"operation": "vectors.error", "error_type": type(exc).__name__},
            )
            raise VectorStoreUnavailableError from exc
        except ChromaError as exc:
            logger.warning(
                "vector store request failed",
                extra={"operation": "vectors.error", "error_type": type(exc).__name__},
            )
            raise VectorStoreError(diagnostics=str(exc)[:_MAX_DIAGNOSTIC_CHARACTERS]) from exc


class VectorStoreProbe:
    """Readiness probe for ``/health/ready``: the vector store answers and the index opens."""

    name = PROBE_NAME

    def __init__(self, store: ChromaVectorStore) -> None:
        self._store = store

    async def check(self) -> None:
        await self._store.check()


def _ensure_owned(owner_id: UUID, records: Sequence[VectorRecord]) -> None:
    for record in records:
        if record.metadata.user_id != owner_id:
            message = "record owner differs from the caller"
            raise VectorOwnershipConflictError(diagnostics=message)
    if len({record.id for record in records}) != len(records):
        message = "duplicate vector ids in one upsert"
        raise InvalidVectorRecordError(message)


def _owner_clause(owner_id: UUID) -> Any:  # noqa: ANN401 - Chroma's Where type is a deep TypedDict union
    return {"user_id": str(owner_id)}


def _where_clause(scope: SearchFilter) -> Any:  # noqa: ANN401 - see _owner_clause
    """Chroma's filter: the owner first, always; the rest narrows within that scope."""
    clauses: list[dict[str, Any]] = [_owner_clause(scope.owner_id)]
    if scope.document_ids is not None:
        ids = [str(document_id) for document_id in scope.document_ids]
        clauses.append({"document_id": {"$in": ids or ["__none__"]}})
    if scope.collection_id is not None:
        clauses.append({"collection_id": str(scope.collection_id)})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


__all__ = ["ChromaVectorStore", "VectorStoreProbe"]
