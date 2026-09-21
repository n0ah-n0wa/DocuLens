"""Retrieval concepts (SPECIFICATIONS.md §17, §18, §20, §22, §23, §27).

The pure parts of the retrieval pipeline live here so they can be tested without any port:

- :func:`prepare_query` normalises an untrusted question deterministically;
- :class:`RetrievalScope` expresses what §27 lets a user ask across, and :class:`ResolvedScope`
  is the set of ``READY`` documents that scope actually maps to;
- :func:`select_candidates` turns raw vector hits into ranked :class:`Evidence`, keeping only
  hits whose chunk still exists (in PostgreSQL, the source of truth) inside the resolved scope;
- :func:`assemble_context` builds the bounded, de-duplicated, deterministic context of §20.

Evidence text is document content and therefore untrusted data (§22); nothing here interprets
it, and the prompt builder must present it as data, never as instructions.
"""

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from doculens.domain.documents import Document, DocumentChunk, ProcessingStatus
from doculens.domain.errors import DependencyUnavailableError, InvalidInputError
from doculens.domain.ingestion import sanitize_page_text
from doculens.domain.vectors import SearchFilter, SearchHit

_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"[^\W_]+")
MAX_KEYWORD_TERMS = 32
MAX_KEYWORD_TERM_LENGTH = 64  # well inside the full-text engine's lexeme limit
RRF_K = 60


class InvalidQueryError(InvalidInputError):
    code = "INVALID_QUERY"
    default_message = "The question is empty or too long."


class InvalidRetrievalScopeError(InvalidInputError):
    code = "INVALID_RETRIEVAL_SCOPE"
    default_message = "The retrieval scope is not valid."


class RetrievalTimeoutError(DependencyUnavailableError):
    """The retrievers did not answer within the per-question budget; retryable (§67)."""

    code = "RETRIEVAL_TIMEOUT"
    default_message = "Retrieval took too long; please try again."


class RetrievalStrategy(StrEnum):
    """Which retrievers answer a question (§18); selected by configuration."""

    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class RetrievalLimits:
    """Bounds of one retrieval; injected from configuration, never hard-coded."""

    max_query_characters: int = 2_000
    candidate_limit: int = 20
    min_score: float = 0.0
    max_scope_documents: int = 100
    timeout_seconds: float = 15.0  # wall-clock budget for the retrievers of one question

    def __post_init__(self) -> None:
        if (
            self.max_query_characters < 1
            or self.candidate_limit < 1
            or self.max_scope_documents < 1
        ):
            message = "retrieval limits must be positive"
            raise ValueError(message)
        if not -1.0 <= self.min_score <= 1.0:
            message = "the minimum score is a cosine similarity in [-1, 1]"
            raise ValueError(message)
        if not self.timeout_seconds > 0:
            message = "the retrieval timeout must be positive"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """What §20 makes the context builder enforce."""

    max_chunks: int = 5
    max_characters: int = 12_000
    max_chunks_per_document: int = 3

    def __post_init__(self) -> None:
        if self.max_chunks < 1 or self.max_characters < 1 or self.max_chunks_per_document < 1:
            message = "context limits must be positive"
            raise ValueError(message)


# -- query preprocessing --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    """The user's question as typed (persisted later, §25) and the text that is embedded."""

    original: str
    text: str


def prepare_query(question: str, *, max_characters: int) -> PreparedQuery:
    """Deterministic normalisation of an untrusted question.

    Control characters and lone surrogates are removed, compatibility forms are folded (NFKC, so
    full-width digits and ligatures embed like their plain forms), whitespace is collapsed and
    the result is bounded; case is kept because embedding models are case-aware. An empty or
    over-long question is refused before any provider call.
    """
    cleaned, truncated = sanitize_page_text(question, max_characters=max_characters)
    if truncated:
        message = f"the question exceeds {max_characters} characters"
        raise InvalidQueryError(message)
    text = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", cleaned)).strip()
    if not text:
        message = "the question is empty"
        raise InvalidQueryError(message)
    return PreparedQuery(original=question, text=text)


# -- keyword retrieval and fusion (§18) -----------------------------------------------------------


def words_of(text: str) -> list[str]:
    """Lower-cased words (letters and digits, any script) in order of appearance."""
    return _WORD.findall(text.lower())


def keyword_terms(text: str) -> tuple[str, ...]:
    """The distinct terms a keyword retriever matches, in query order and bounded.

    Any term may match (OR semantics), and chunks matching more terms rank higher; the same
    function feeds the SQL adapter and the in-memory fake so both agree on what a term is.
    Over-long "words" (pasted identifiers, hashes) are ignored rather than sent to the engine.
    """
    words = (word for word in words_of(text) if len(word) <= MAX_KEYWORD_TERM_LENGTH)
    return tuple(dict.fromkeys(words))[:MAX_KEYWORD_TERMS]


@dataclass(frozen=True, slots=True)
class ChunkMatch:
    """A chunk found by keyword search with its rank score: higher is better, not a similarity."""

    chunk: DocumentChunk
    score: float


def fuse_rankings(
    rankings: Sequence[Sequence[SearchHit]], *, limit: int, k: int = RRF_K
) -> list[SearchHit]:
    """Reciprocal-rank fusion: a hit scores ``sum(1 / (k + rank))`` over the rankings it is in,
    scaled so that a hit ranked first by every ranking scores ``1.0``.

    Rank-based, so cosine similarities and full-text ranks combine without calibration, and a
    chunk found by several retrievers outranks one found by a single retriever. The hit's
    metadata comes from the first ranking that contains it; ties break on the vector id.
    """
    if not rankings:
        return []
    ceiling = len(rankings) / (k + 1)
    scores: dict[str, float] = {}
    hits: dict[str, SearchHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, 1):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)
            hits.setdefault(hit.id, hit)
    fused = [replace(hits[hit_id], score=score / ceiling) for hit_id, score in scores.items()]
    fused.sort(key=lambda hit: (-hit.score, hit.id))
    return fused[: max(0, limit)]


# -- scope (§27) ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """What the question may be answered from: selected documents, one collection, or every
    document of the owner. Selecting documents and a collection at once is refused rather than
    guessed at (the specification names the three scopes separately)."""

    owner_id: UUID
    document_ids: tuple[UUID, ...] | None = None
    collection_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.document_ids is not None:
            if not self.document_ids:
                message = "at least one document must be selected"
                raise InvalidRetrievalScopeError(message)
            if self.collection_id is not None:
                message = "select documents or a collection, not both"
                raise InvalidRetrievalScopeError(message)
            unique = tuple(dict.fromkeys(self.document_ids))
            object.__setattr__(self, "document_ids", unique)


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The ``READY`` documents a scope maps to, after ownership was checked at the database.

    Every retriever is then restricted to exactly these document ids, so a document that is
    being re-processed, has failed or is being deleted is never served, whatever its vectors say.
    The collection is not repeated in the store filter: the database already resolved it, and a
    store whose collection metadata lags behind a move must not hide evidence.
    """

    scope: RetrievalScope
    documents: Mapping[UUID, Document]
    excluded: int = 0  # documents in scope that are not READY

    @property
    def is_empty(self) -> bool:
        return not self.documents

    def search_filter(self) -> SearchFilter:
        return SearchFilter(
            owner_id=self.scope.owner_id,
            document_ids=tuple(sorted(self.documents, key=lambda document_id: document_id.hex)),
        )


def resolve_scope(scope: RetrievalScope, candidates: Sequence[Document]) -> ResolvedScope:
    """Keep the owner's ``READY`` documents among ``candidates`` (those the scope named)."""
    ready = {
        document.id: document
        for document in candidates
        if document.owner_id == scope.owner_id
        and document.processing_status is ProcessingStatus.READY
    }
    excluded = sum(1 for document in candidates if document.id not in ready)
    return ResolvedScope(scope=scope, documents=MappingProxyType(ready), excluded=excluded)


# -- evidence (§20, §23) --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Evidence:
    """One retrieved chunk with everything a citation (§23) and a context item (§20) need."""

    document_id: UUID
    chunk_id: UUID
    page_number: int
    text: str
    score: float
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return str(self.metadata.get("filename", ""))

    @property
    def chunk_index(self) -> int:
        value = self.metadata.get("chunk_index", 0)
        return value if isinstance(value, int) else 0


def evidence_from(
    hit: SearchHit, chunk: DocumentChunk, document: Document, *, rank: int, retriever: str
) -> Evidence:
    """Evidence built from the chunk row (source of truth for text and page), not the vector."""
    page_number = chunk.metadata.get("page_number")
    metadata: dict[str, object] = {
        "filename": document.filename,
        "collection_id": str(document.collection_id) if document.collection_id else None,
        "chunk_index": chunk.chunk_index,
        "token_count": chunk.token_count,
        "content_hash": str(chunk.metadata.get("content_hash", "")),
        "section_title": chunk.metadata.get("section_title"),
        "rank": rank,
        "retriever": retriever,
        "embedding_model": hit.metadata.embedding_model,
    }
    return Evidence(
        document_id=document.id,
        chunk_id=chunk.id,
        page_number=page_number if isinstance(page_number, int) else hit.page_number,
        text=chunk.text,
        score=hit.score,
        metadata=MappingProxyType(metadata),
    )


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    evidence: tuple[Evidence, ...]
    out_of_scope: int = 0  # the hit's document is not in the resolved scope (or owner differs)
    stale: int = 0  # the chunk no longer exists or its text changed since it was indexed
    duplicates: int = 0


def _content_key(chunk: DocumentChunk) -> str:
    recorded = chunk.metadata.get("content_hash")
    if isinstance(recorded, str) and recorded:
        return recorded
    return hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()


def select_candidates(
    hits: Sequence[SearchHit],
    *,
    scope: ResolvedScope,
    chunks: Mapping[UUID, DocumentChunk],
    retriever: str = "vector",
) -> CandidateSelection:
    """Rank hits deterministically and keep those that are still valid evidence.

    A hit survives only if its document is in the resolved (owned, ``READY``) scope, its chunk
    row exists under that document with the same content hash the vector was built from, and
    neither the chunk nor identical text was kept already. Score thresholds are a retriever's
    business (a similarity means nothing to a keyword rank), so none is applied here.
    """
    ordered = sorted(hits, key=lambda hit: (-hit.score, hit.id))
    evidence: list[Evidence] = []
    counts: Counter[str] = Counter()
    seen_chunks: set[UUID] = set()
    seen_content: set[str] = set()
    for hit in ordered:
        document = scope.documents.get(hit.document_id)
        if document is None or hit.metadata.user_id != scope.scope.owner_id:
            counts["out_of_scope"] += 1
            continue
        chunk = chunks.get(hit.chunk_id)
        if chunk is None or chunk.document_id != document.id or _is_stale(hit, chunk):
            counts["stale"] += 1
            continue
        content = _content_key(chunk)
        if chunk.id in seen_chunks or content in seen_content:
            counts["duplicates"] += 1
            continue
        seen_chunks.add(chunk.id)
        seen_content.add(content)
        evidence.append(
            evidence_from(hit, chunk, document, rank=len(evidence) + 1, retriever=retriever)
        )
    return CandidateSelection(
        evidence=tuple(evidence),
        out_of_scope=counts["out_of_scope"],
        stale=counts["stale"],
        duplicates=counts["duplicates"],
    )


def _is_stale(hit: SearchHit, chunk: DocumentChunk) -> bool:
    """The vector was computed for other text than the chunk now holds."""
    indexed = hit.metadata.content_hash
    return bool(indexed) and indexed != _content_key(chunk)


# -- context assembly (§20) -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextItem:
    """A numbered context entry; the number is what citations refer to (OQ-30)."""

    index: int
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class AssembledContext:
    items: tuple[ContextItem, ...]
    characters: int
    omitted: int  # distinct evidence left out by the limits

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def document_ids(self) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(item.evidence.document_id for item in self.items))


def assemble_context(evidence: Sequence[Evidence], *, limits: ContextLimits) -> AssembledContext:
    """Deterministic context from ranked evidence.

    ``evidence`` is consumed in the order given (its rank order) under the chunk and character
    budgets; a chunk seen twice counts once. When the evidence spans several documents, no
    document takes more than ``max_chunks_per_document`` slots while others still wait (source
    diversity); whatever budget remains is then filled in rank order regardless of document.
    The output keeps rank order, so context item ``n`` is the best evidence that fit at that
    position and equal inputs always produce equal output.
    """
    distinct: dict[UUID, Evidence] = {}
    for item in evidence:
        distinct.setdefault(item.chunk_id, item)
    ranked = list(enumerate(distinct.values()))
    multi_source = len({item.document_id for _, item in ranked}) > 1
    chosen: list[tuple[int, Evidence]] = []
    characters = 0
    per_document: Counter[UUID] = Counter()
    deferred: list[tuple[int, Evidence]] = []

    def fits(item: Evidence) -> bool:
        return len(chosen) < limits.max_chunks and characters + len(item.text) <= (
            limits.max_characters
        )

    for position, item in ranked:
        if multi_source and per_document[item.document_id] >= limits.max_chunks_per_document:
            deferred.append((position, item))
            continue
        if fits(item):
            chosen.append((position, item))
            characters += len(item.text)
            per_document[item.document_id] += 1
    for position, item in deferred:
        if fits(item):
            chosen.append((position, item))
            characters += len(item.text)
    chosen.sort(key=lambda pair: pair[0])
    items = tuple(
        ContextItem(index=number, evidence=item) for number, (_, item) in enumerate(chosen, 1)
    )
    return AssembledContext(items=items, characters=characters, omitted=len(ranked) - len(chosen))


__all__ = [
    "MAX_KEYWORD_TERMS",
    "MAX_KEYWORD_TERM_LENGTH",
    "RRF_K",
    "AssembledContext",
    "CandidateSelection",
    "ChunkMatch",
    "ContextItem",
    "ContextLimits",
    "Evidence",
    "InvalidQueryError",
    "InvalidRetrievalScopeError",
    "PreparedQuery",
    "ResolvedScope",
    "RetrievalLimits",
    "RetrievalScope",
    "RetrievalStrategy",
    "RetrievalTimeoutError",
    "assemble_context",
    "evidence_from",
    "fuse_rankings",
    "keyword_terms",
    "prepare_query",
    "resolve_scope",
    "select_candidates",
    "words_of",
]
