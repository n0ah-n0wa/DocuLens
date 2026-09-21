"""The RAG service boundary (SPECIFICATIONS.md §17 to §28, §72, §73).

Everything outside the application layer (API routes, evaluation harness) talks to
retrieval-augmented generation through :class:`RagService` and the plain data types below.
Behind the boundary sit the §17 stages and the answering pipeline; in front of it nobody sees a
vector store, an embedding provider, a language model or a prompt.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from doculens.application.answering import AnswerService
from doculens.application.retrieval import RetrievalResult, RetrievalService
from doculens.domain.answering import AnswerResult
from doculens.domain.retrieval import RetrievalScope


@dataclass(frozen=True, slots=True)
class RagQuery:
    """A question and what it may be answered from (§27), as the transport layer collects it."""

    owner_id: UUID
    question: str
    document_ids: Sequence[UUID] | None = None
    collection_id: UUID | None = None

    def scope(self) -> RetrievalScope:
        """Validated scope; raises ``InvalidRetrievalScopeError`` for an unusable selection."""
        return RetrievalScope(
            owner_id=self.owner_id,
            document_ids=tuple(self.document_ids) if self.document_ids is not None else None,
            collection_id=self.collection_id,
        )


class RagService:
    def __init__(self, *, retrieval: RetrievalService, answering: AnswerService) -> None:
        self._retrieval = retrieval
        self._answering = answering

    async def retrieve(self, query: RagQuery) -> RetrievalResult:
        """Structured evidence for the question, scoped to the caller's own READY documents."""
        scope = query.scope()
        return await self._retrieval.retrieve(
            scope.owner_id,
            query.question,
            document_ids=scope.document_ids,
            collection_id=scope.collection_id,
        )

    async def answer(self, query: RagQuery, *, conversation_id: UUID | None = None) -> AnswerResult:
        """A grounded, cited answer persisted in the conversation (§21, §23, §28)."""
        scope = query.scope()
        return await self._answering.answer(
            scope.owner_id,
            query.question,
            conversation_id=conversation_id,
            document_ids=scope.document_ids,
            collection_id=scope.collection_id,
        )


__all__ = ["RagQuery", "RagService"]
