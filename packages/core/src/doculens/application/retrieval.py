"""Retrieval pipeline (SPECIFICATIONS.md §17, §18, §20, §27, §72).

One component per stage of §17, each independently testable, composed by
:class:`RetrievalService`:

1. :class:`QueryPreprocessor` — normalises the question (domain rules);
2. :class:`ScopeResolver` — metadata filtering: maps the requested scope to the owner's ``READY``
   documents through the repositories, so every retriever is restricted to exactly those ids;
3. a :class:`Retriever` — the §18 abstraction. :class:`VectorRetriever` embeds the query and
   searches the vector store; :class:`KeywordRetriever` matches the query's terms against the
   chunk rows through the repository port; :class:`HybridRetriever` runs both and fuses their
   rankings. :func:`build_retriever` picks one from the configured :class:`RetrievalStrategy`;
4. :class:`CandidateSelector` — loads the hit chunks from PostgreSQL (owner-scoped, the source of
   truth for text) and applies staleness and duplicate rules;
5. an optional :class:`RerankingStage` (§19): the selected candidates are the top-N, the
   provider's top-k become the final evidence set; disabled or failing reranking leaves the
   retrieval order in place;
6. :class:`ContextAssembler` — the deterministic, bounded context of §20.

No database transaction is held across the embedding or vector-store call: scope resolution,
keyword search and chunk loading are short read-only units of work.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from doculens.application.embeddings import EmbeddingProvider
from doculens.application.reranking import RerankingStage
from doculens.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from doculens.application.vectors import VectorStore
from doculens.domain.collections import CollectionNotFoundError
from doculens.domain.documents import Document, DocumentNotFoundError
from doculens.domain.embeddings import EmbeddingUsage
from doculens.domain.reranking import RerankingReport, RerankingStatus
from doculens.domain.retrieval import (
    AssembledContext,
    CandidateSelection,
    ChunkMatch,
    ContextLimits,
    Evidence,
    InvalidRetrievalScopeError,
    PreparedQuery,
    ResolvedScope,
    RetrievalLimits,
    RetrievalScope,
    RetrievalStrategy,
    RetrievalTimeoutError,
    assemble_context,
    fuse_rankings,
    keyword_terms,
    prepare_query,
    resolve_scope,
    select_candidates,
)
from doculens.domain.vectors import SearchFilter, SearchHit, VectorMetadata, vector_id_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Retrieved:
    """Raw hits of one retriever, best first, plus what producing them cost."""

    hits: list[SearchHit]
    usage: EmbeddingUsage = field(default_factory=EmbeddingUsage)
    below_threshold: int = 0  # semantic hits dropped by the similarity threshold


class Retriever(Protocol):
    """The retrieval abstraction of §18: a store-agnostic way to find candidate chunks."""

    @property
    def name(self) -> str: ...

    async def retrieve(
        self, query: PreparedQuery, *, scope: SearchFilter, limit: int
    ) -> Retrieved: ...


class QueryPreprocessor:
    def __init__(self, *, max_characters: int) -> None:
        self._max_characters = max_characters

    def prepare(self, question: str) -> PreparedQuery:
        return prepare_query(question, max_characters=self._max_characters)


class VectorRetriever:
    """Semantic retrieval: embed the query, then nearest neighbours within the owner scope.

    ``min_score`` is a cosine similarity below which a neighbour is not evidence (OQ-29).
    """

    name = "vector"

    def __init__(
        self, *, embeddings: EmbeddingProvider, vectors: VectorStore, min_score: float = -1.0
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        self._min_score = min_score

    async def retrieve(self, query: PreparedQuery, *, scope: SearchFilter, limit: int) -> Retrieved:
        embedded = await self._embeddings.embed_query(query.text)
        hits = await self._vectors.search(embedded.single, scope=scope, limit=limit)
        kept = [hit for hit in hits if hit.score >= self._min_score]
        return Retrieved(hits=kept, usage=embedded.usage, below_threshold=len(hits) - len(kept))


class KeywordRetriever:
    """Lexical retrieval over the chunk rows: any query term matches, more terms rank higher.

    The repository port hides the search engine (PostgreSQL full-text search in production, a
    word-overlap fake in tests); scores are rank values, not similarities.
    """

    name = "keyword"

    def __init__(self, *, unit_of_work: UnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def retrieve(self, query: PreparedQuery, *, scope: SearchFilter, limit: int) -> Retrieved:
        if not keyword_terms(query.text):
            return Retrieved(hits=[])
        async with self._unit_of_work() as uow:
            matches = await uow.document_content.search_chunks(query.text, scope=scope, limit=limit)
        return Retrieved(hits=[_hit_from_match(match, scope.owner_id) for match in matches])


def _hit_from_match(match: ChunkMatch, owner_id: UUID) -> SearchHit:
    chunk = match.chunk
    page_number = chunk.metadata.get("page_number")
    metadata = VectorMetadata(
        user_id=owner_id,
        document_id=chunk.document_id,
        chunk_id=chunk.id,
        chunk_index=chunk.chunk_index,
        page_number=page_number if isinstance(page_number, int) else 0,
        content_hash=str(chunk.metadata.get("content_hash", "")),
    )
    return SearchHit(id=vector_id_for(chunk.id), score=match.score, metadata=metadata)


class HybridRetriever:
    """Semantic and keyword retrieval side by side, combined by reciprocal-rank fusion (§18).

    Each retriever gets the full candidate limit; a chunk found by both ranks above one found by
    either, and the fused ranking is cut to the limit again.
    """

    name = "hybrid"

    def __init__(self, *, semantic: Retriever, keyword: Retriever) -> None:
        self._semantic = semantic
        self._keyword = keyword

    async def retrieve(self, query: PreparedQuery, *, scope: SearchFilter, limit: int) -> Retrieved:
        try:
            async with asyncio.TaskGroup() as group:
                semantic_task = group.create_task(
                    self._semantic.retrieve(query, scope=scope, limit=limit)
                )
                keyword_task = group.create_task(
                    self._keyword.retrieve(query, scope=scope, limit=limit)
                )
        except ExceptionGroup as failures:
            # One side failed and the group cancelled the other; report the failure itself.
            raise failures.exceptions[0] from failures
        semantic, keyword = semantic_task.result(), keyword_task.result()
        return Retrieved(
            hits=fuse_rankings([semantic.hits, keyword.hits], limit=limit),
            usage=semantic.usage + keyword.usage,
            below_threshold=semantic.below_threshold + keyword.below_threshold,
        )


def build_retriever(
    strategy: RetrievalStrategy,
    *,
    unit_of_work: UnitOfWorkFactory,
    embeddings: EmbeddingProvider,
    vectors: VectorStore,
    min_score: float,
) -> Retriever:
    """The retriever a configured strategy stands for."""
    if strategy is RetrievalStrategy.KEYWORD:
        return KeywordRetriever(unit_of_work=unit_of_work)
    semantic = VectorRetriever(embeddings=embeddings, vectors=vectors, min_score=min_score)
    if strategy is RetrievalStrategy.SEMANTIC:
        return semantic
    return HybridRetriever(semantic=semantic, keyword=KeywordRetriever(unit_of_work=unit_of_work))


class ScopeResolver:
    """Metadata filtering stage: the documents a question may draw on (§16, §27).

    Ownership is enforced by the repositories: an unknown or foreign document or collection is
    reported as not found, never as forbidden (ADR-013). Only ``READY`` documents remain.
    """

    def __init__(self, *, max_documents: int) -> None:
        self._max_documents = max_documents

    async def resolve(self, unit_of_work: UnitOfWork, scope: RetrievalScope) -> ResolvedScope:
        return resolve_scope(scope, await self._candidates(unit_of_work, scope))

    async def _candidates(self, uow: UnitOfWork, scope: RetrievalScope) -> list[Document]:
        if scope.document_ids is not None:
            if len(scope.document_ids) > self._max_documents:
                message = f"at most {self._max_documents} documents can be selected"
                raise InvalidRetrievalScopeError(message)
            # One query for the owner's documents instead of one per selected id.
            owned = {d.id: d for d in await uow.documents.list_for_owner(scope.owner_id)}
            if any(document_id not in owned for document_id in scope.document_ids):
                raise DocumentNotFoundError
            return [owned[document_id] for document_id in scope.document_ids]
        if scope.collection_id is not None:
            if await uow.collections.get(scope.owner_id, scope.collection_id) is None:
                raise CollectionNotFoundError
            return await uow.documents.list_in_collection(scope.owner_id, scope.collection_id)
        return await uow.documents.list_for_owner(scope.owner_id)


class CandidateSelector:
    async def select(
        self,
        unit_of_work: UnitOfWork,
        hits: Sequence[SearchHit],
        scope: ResolvedScope,
        *,
        retriever: str,
    ) -> CandidateSelection:
        wanted = list(dict.fromkeys(hit.chunk_id for hit in hits))
        rows = await unit_of_work.document_content.get_chunks(scope.scope.owner_id, wanted)
        chunks = {chunk.id: chunk for chunk in rows}
        return select_candidates(hits, scope=scope, chunks=chunks, retriever=retriever)


class ContextAssembler:
    def __init__(self, limits: ContextLimits) -> None:
        self._limits = limits

    def assemble(self, evidence: Sequence[Evidence]) -> AssembledContext:
        return assemble_context(evidence, limits=self._limits)


@dataclass(frozen=True, slots=True)
class RetrievalStats:
    documents_in_scope: int
    documents_excluded: int  # in scope but not READY
    hits: int
    below_threshold: int
    out_of_scope: int
    stale: int
    duplicates: int
    omitted_from_context: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Structured evidence for one question; nothing here is a bare string of document text."""

    query: PreparedQuery
    scope: RetrievalScope
    retriever: str
    evidence: tuple[Evidence, ...]  # the final evidence set (after reranking when applied)
    context: AssembledContext
    usage: EmbeddingUsage
    stats: RetrievalStats
    reranking: RerankingReport = field(
        default_factory=lambda: RerankingReport(status=RerankingStatus.DISABLED)
    )

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)


class RetrievalService:
    def __init__(  # noqa: PLR0913 - one argument per pipeline stage, wired by the composition root
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        retriever: Retriever,
        limits: RetrievalLimits,
        context_limits: ContextLimits,
        preprocessor: QueryPreprocessor | None = None,
        scope_resolver: ScopeResolver | None = None,
        selector: CandidateSelector | None = None,
        assembler: ContextAssembler | None = None,
        reranking: RerankingStage | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._retriever = retriever
        self._limits = limits
        self._preprocessor = preprocessor or QueryPreprocessor(
            max_characters=limits.max_query_characters
        )
        self._scope_resolver = scope_resolver or ScopeResolver(
            max_documents=limits.max_scope_documents
        )
        self._selector = selector or CandidateSelector()
        self._assembler = assembler or ContextAssembler(context_limits)
        self._reranking = reranking

    async def retrieve(
        self,
        owner_id: UUID,
        question: str,
        *,
        document_ids: Sequence[UUID] | None = None,
        collection_id: UUID | None = None,
    ) -> RetrievalResult:
        """Evidence for ``question`` within the owner's scope; empty when nothing qualifies."""
        started = time.perf_counter()
        query = self._preprocessor.prepare(question)
        scope = RetrievalScope(
            owner_id=owner_id,
            document_ids=tuple(document_ids) if document_ids is not None else None,
            collection_id=collection_id,
        )
        async with self._unit_of_work() as uow:
            resolved = await self._scope_resolver.resolve(uow, scope)
        if resolved.is_empty:
            # No READY document can answer: no provider call, no cost (§38).
            retrieved = Retrieved(hits=[])
            selection = CandidateSelection(evidence=())
        else:
            try:
                async with asyncio.timeout(self._limits.timeout_seconds):
                    retrieved = await self._retriever.retrieve(
                        query,
                        scope=resolved.search_filter(),
                        limit=self._limits.candidate_limit,
                    )
            except TimeoutError as exc:
                raise RetrievalTimeoutError from exc
            async with self._unit_of_work() as uow:
                selection = await self._selector.select(
                    uow, retrieved.hits, resolved, retriever=self._retriever.name
                )
        evidence = selection.evidence
        report = RerankingReport(status=RerankingStatus.DISABLED)
        if self._reranking is not None:
            evidence, report = await self._reranking.rerank(query, evidence)
        context = self._assembler.assemble(evidence)
        stats = RetrievalStats(
            documents_in_scope=len(resolved.documents),
            documents_excluded=resolved.excluded,
            hits=len(retrieved.hits),
            below_threshold=retrieved.below_threshold,
            out_of_scope=selection.out_of_scope,
            stale=selection.stale,
            duplicates=selection.duplicates,
            omitted_from_context=context.omitted,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        logger.info(
            "retrieval completed",
            extra={
                "operation": "retrieval.retrieve",
                "user_id": str(owner_id),
                "retriever": self._retriever.name,
                "documents_in_scope": stats.documents_in_scope,
                "hits": stats.hits,
                "evidence": len(evidence),
                "reranking": report.status.value,
                "context_items": len(context.items),
                "context_characters": context.characters,
                "latency_ms": stats.latency_ms,
            },
        )
        return RetrievalResult(
            query=query,
            scope=scope,
            retriever=self._retriever.name,
            evidence=evidence,
            context=context,
            usage=retrieved.usage,
            stats=stats,
            reranking=report,
        )


__all__ = [
    "CandidateSelector",
    "ContextAssembler",
    "HybridRetriever",
    "KeywordRetriever",
    "QueryPreprocessor",
    "RetrievalResult",
    "RetrievalService",
    "RetrievalStats",
    "Retrieved",
    "Retriever",
    "ScopeResolver",
    "VectorRetriever",
    "build_retriever",
]
