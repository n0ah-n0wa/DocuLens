"""Question answering (SPECIFICATIONS.md §21 to §26, §28, §38, §67).

The pipeline of §17 carried through to an answer:

    question → query rewriting (follow-ups only) → retrieval (with reranking and context
    assembly) → prompt construction → generation → reference validation → citations →
    persistence of the user and assistant messages with their citations

Rules the service enforces:

- when retrieval yields no context, no model is called and the answer is the prescribed
  insufficient-evidence statement (OQ-29); when the model answers with that statement, the
  outcome is reported as insufficient evidence and nothing is cited;
- a ``[n]`` reference is a citation only if ``n`` is a context item of this prompt; the rest
  are removed from the answer and counted (OQ-30);
- query rewriting fails open: any provider trouble, timeout or implausible rewrite means the
  question is retrieved for as asked;
- generation failures propagate (a 503 the client can retry) and persist nothing: the
  conversation never shows a question without its answer (§67, OQ-18);
- the user message keeps the question as asked; the rewritten query is never shown (§26);
- an answer that reproduces the system policy (a document or the question asked the model to
  reveal it) is withheld: outcome ``blocked``, a fixed statement, no citations (§22).
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from doculens.application.auth import Clock
from doculens.application.documents import ensure_collection_owned
from doculens.application.llm import LLMProvider
from doculens.application.metrics import (
    NAMESPACE_AI,
    NAMESPACE_RAG,
    emit_count,
    emit_latency_ms,
)
from doculens.application.retrieval import RetrievalResult, RetrievalService
from doculens.application.tracing import set_span_attributes, start_span
from doculens.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from doculens.domain.answering import (
    BLOCKED_ANSWER_STATEMENT,
    AnswerOutcome,
    AnswerResult,
    AnswerTiming,
    AnswerToken,
    AnswerUsage,
    GenerationTimeoutError,
    RetrievalMetadata,
    RewriteVerdict,
    accept_rewrite,
    build_citations,
    build_rewrite_messages,
    clean_references,
    detect_violation,
    is_insufficient,
    normalise_text,
)
from doculens.domain.common import title_from_text
from doculens.domain.conversations import (
    MAX_CONVERSATION_TITLE_LENGTH,
    Citation,
    Conversation,
    ConversationNotFoundError,
    Message,
    MessageRole,
)
from doculens.domain.errors import DependencyUnavailableError, InvalidInputError
from doculens.domain.ids import new_id
from doculens.domain.llm import (
    ChatMessage,
    Generation,
    GenerationOptions,
    LLMError,
    LLMResponseInvalidError,
    LLMUsage,
)
from doculens.domain.prompting import INSUFFICIENT_EVIDENCE_STATEMENT, GroundedPrompt, PromptBuilder
from doculens.domain.time import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Rewrite:
    query: str
    applied: bool
    usage: LLMUsage
    latency_ms: int
    reason: str = "no_history"  # why the question was or was not rewritten


class QueryRewriter(Protocol):
    async def rewrite(self, question: str, history: Sequence[ChatMessage]) -> Rewrite:
        """The query to retrieve for; ``applied`` is False when the question is used as is."""
        ...


class NoQueryRewriting:
    async def rewrite(self, question: str, history: Sequence[ChatMessage]) -> Rewrite:
        del history
        return Rewrite(question, applied=False, usage=LLMUsage(), latency_ms=0, reason="disabled")


class LLMQueryRewriter:
    """Rewrites follow-up questions into standalone queries with the language model (§26).

    Only questions with conversation history are rewritten; the rewrite is accepted only when
    it is plausible, and every failure or timeout leaves the original question in place.
    """

    def __init__(self, llm: LLMProvider, *, max_characters: int, timeout_seconds: float) -> None:
        self._llm = llm
        self._max_characters = max_characters
        self._timeout_seconds = timeout_seconds

    async def rewrite(self, question: str, history: Sequence[ChatMessage]) -> Rewrite:
        if not history:
            return Rewrite(query=question, applied=False, usage=LLMUsage(), latency_ms=0)
        started = time.perf_counter()
        usage = LLMUsage()
        verdict = RewriteVerdict(None, "provider_failure")
        try:
            async with asyncio.timeout(self._timeout_seconds):
                generation = await self._llm.generate(
                    build_rewrite_messages(question, history),
                    options=GenerationOptions(max_output_tokens=256, temperature=0.0),
                )
            usage = generation.usage
            verdict = accept_rewrite(
                generation.text,
                question,
                max_characters=self._max_characters,
                history=history,
            )
        except (TimeoutError, LLMError, DependencyUnavailableError, InvalidInputError) as exc:
            # Provider trouble, a slow provider or a prompt the provider refuses (for example a
            # history beyond its input limit): retrieve for the question as asked.
            code = (
                exc.code
                if isinstance(exc, LLMError | DependencyUnavailableError | InvalidInputError)
                else "TIMEOUT"
            )
            logger.warning(
                "query rewriting skipped",
                extra={"operation": "answer.rewrite_skipped", "error_code": code},
            )
        latency = int((time.perf_counter() - started) * 1000)
        if verdict.text is None:
            logger.info(
                "query rewrite not used",
                extra={"operation": "answer.rewrite_rejected", "reason": verdict.reason},
            )
            return Rewrite(
                question, applied=False, usage=usage, latency_ms=latency, reason=verdict.reason
            )
        if normalise_text(verdict.text).lower() == normalise_text(question).lower():
            return Rewrite(
                question, applied=False, usage=usage, latency_ms=latency, reason="unchanged"
            )
        return Rewrite(
            verdict.text, applied=True, usage=usage, latency_ms=latency, reason="accepted"
        )


@dataclass(frozen=True, slots=True)
class AnswerLimits:
    max_quote_characters: int = 500
    max_history_messages: int = 10  # turns handed to the rewriter and the prompt builder
    max_history_characters: int = 8_000  # and their total size, newest kept
    generation_timeout_seconds: float = 90.0  # wall-clock budget for the answer's model call

    def __post_init__(self) -> None:
        if (
            self.max_quote_characters < 1
            or self.max_history_messages < 0
            or self.max_history_characters < 0
        ):
            message = "answer limits must be positive"
            raise ValueError(message)
        if not self.generation_timeout_seconds > 0:
            message = "the generation timeout must be positive"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class _ConversationState:
    conversation: Conversation
    is_new: bool
    history: tuple[ChatMessage, ...]
    last_message_at: datetime | None = None  # the newest persisted turn, for ordering


class AnswerService:
    def __init__(  # noqa: PLR0913 - one collaborator per pipeline stage, wired by the root
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        retrieval: RetrievalService,
        prompt_builder: PromptBuilder,
        llm: LLMProvider,
        rewriter: QueryRewriter | None = None,
        options: GenerationOptions | None = None,
        limits: AnswerLimits | None = None,
        clock: Clock = utc_now,
        id_factory: Callable[[], UUID] = new_id,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._retrieval = retrieval
        self._prompt_builder = prompt_builder
        self._llm = llm
        self._rewriter = rewriter or NoQueryRewriting()
        self._options = options or GenerationOptions()
        self._limits = limits or AnswerLimits()
        self._clock = clock
        self._new_id = id_factory

    async def answer(
        self,
        owner_id: UUID,
        question: str,
        *,
        conversation_id: UUID | None = None,
        document_ids: Sequence[UUID] | None = None,
        collection_id: UUID | None = None,
    ) -> AnswerResult:
        """Answer ``question`` from the owner's documents inside a conversation.

        Without ``conversation_id`` a conversation is created for the question. An explicit
        document or collection scope wins; otherwise the conversation's collection, if any,
        scopes retrieval; otherwise every READY document of the owner.
        """
        with start_span(
            "answering.answer",
            attributes={
                "user_id": str(owner_id),
                "conversation_id": str(conversation_id) if conversation_id else None,
            },
        ) as root:
            result = await self._answer(
                owner_id,
                question,
                conversation_id=conversation_id,
                document_ids=document_ids,
                collection_id=collection_id,
            )
            set_span_attributes(
                root,
                {
                    "outcome": result.outcome.value,
                    "evidence": result.retrieval.evidence,
                    "citations": len(result.citations),
                },
            )
            return result

    async def _answer(
        self,
        owner_id: UUID,
        question: str,
        *,
        conversation_id: UUID | None = None,
        document_ids: Sequence[UUID] | None = None,
        collection_id: UUID | None = None,
    ) -> AnswerResult:
        started = time.perf_counter()
        state = await self._load_conversation(owner_id, question, conversation_id, collection_id)
        if document_ids is None and collection_id is None:
            collection_id = state.conversation.collection_id

        with start_span("answering.rewrite"):
            rewrite = await self._rewriter.rewrite(question, state.history)
        retrieval_started = time.perf_counter()
        retrieval = await self._retrieval.retrieve(
            owner_id, rewrite.query, document_ids=document_ids, collection_id=collection_id
        )
        retrieval_ms = _elapsed_ms(retrieval_started)

        generation_started = time.perf_counter()
        prompt: GroundedPrompt | None = None
        generation: Generation | None = None
        if retrieval.context.is_empty:
            # Nothing to answer from: no model call, no fabrication (§21, OQ-29).
            answer_text = INSUFFICIENT_EVIDENCE_STATEMENT
            outcome = AnswerOutcome.INSUFFICIENT_EVIDENCE
            cited: tuple[int, ...] = ()
            invalid = 0
            violation: str | None = None
        else:
            prompt = self._prompt_builder.build(question, retrieval.context, history=state.history)
            try:
                async with asyncio.timeout(self._limits.generation_timeout_seconds):
                    with start_span(
                        "answering.generate",
                        attributes={"llm.provider": self._llm.name, "llm.model": self._llm.model},
                    ):
                        generation = await self._llm.generate(
                            prompt.messages, options=self._options
                        )
            except TimeoutError as exc:
                raise GenerationTimeoutError from exc
            cleaned = clean_references(generation.text, prompt.citation_indexes)
            invalid = cleaned.invalid_references
            violation = detect_violation(generation.text)
            if violation is not None:
                logger.warning(
                    "answer withheld",
                    extra={
                        "operation": "answer.blocked",
                        "user_id": str(owner_id),
                        "violation": violation,
                    },
                )
                answer_text = BLOCKED_ANSWER_STATEMENT
                outcome = AnswerOutcome.BLOCKED
                cited = ()
            elif is_insufficient(cleaned.text):
                answer_text = cleaned.text
                outcome = AnswerOutcome.INSUFFICIENT_EVIDENCE
                cited = ()
            else:
                answer_text = cleaned.text
                outcome = AnswerOutcome.ANSWERED
                cited = cleaned.cited
        generation_ms = _elapsed_ms(generation_started)

        persistence_started = time.perf_counter()
        with start_span(
            "answering.persist",
            attributes={"conversation_id": str(state.conversation.id)},
        ):
            user_message, assistant_message, citations = await self._persist(
                owner_id, state, question, answer_text, cited, retrieval
            )
        persistence_ms = _elapsed_ms(persistence_started)

        result = AnswerResult(
            outcome=outcome,
            answer=answer_text,
            citations=citations,
            conversation_id=state.conversation.id,
            user_message=user_message,
            assistant_message=assistant_message,
            retrieval=RetrievalMetadata(
                question=question,
                query=rewrite.query,
                rewritten=rewrite.applied,
                retriever=retrieval.retriever,
                documents_in_scope=retrieval.stats.documents_in_scope,
                hits=retrieval.stats.hits,
                evidence=len(retrieval.evidence),
                context_items=len(retrieval.context.items),
                context_characters=retrieval.context.characters,
                reranking=retrieval.reranking,
                prompt_version=prompt.version if prompt else None,
                model=generation.model if generation else None,
                finish_reason=generation.finish_reason if generation else None,
                invalid_references=invalid,
                violation=violation,
            ),
            usage=AnswerUsage(
                embeddings=retrieval.usage,
                generation=generation.usage if generation else LLMUsage(),
                rewriting=rewrite.usage,
            ),
            timing=AnswerTiming(
                rewrite_ms=rewrite.latency_ms,
                retrieval_ms=retrieval_ms,
                generation_ms=generation_ms,
                persistence_ms=persistence_ms,
                total_ms=_elapsed_ms(started),
            ),
        )
        logger.info(
            "question answered",
            extra={
                "operation": "answer.complete",
                "user_id": str(owner_id),
                "conversation_id": str(state.conversation.id),
                "outcome": outcome.value,
                "rewritten": rewrite.applied,
                "evidence": len(retrieval.evidence),
                "citations": len(citations),
                "invalid_references": invalid,
                "model": result.retrieval.model,
                "total_ms": result.timing.total_ms,
                "rewrite_ms": result.timing.rewrite_ms,
                "retrieval_ms": result.timing.retrieval_ms,
                "generation_ms": result.timing.generation_ms,
                "input_tokens": result.usage.generation.input_tokens,
                "output_tokens": result.usage.generation.output_tokens,
            },
        )
        _emit_rag_metrics(result)
        return result

    async def answer_stream(
        self,
        owner_id: UUID,
        question: str,
        *,
        conversation_id: UUID | None = None,
        document_ids: Sequence[UUID] | None = None,
        collection_id: UUID | None = None,
    ) -> AsyncIterator[AnswerToken | AnswerResult]:
        """Stream progressive answer text, then the persisted ``AnswerResult`` (§44).

        Tokens are display-only. The user and assistant messages (and citations) are written
        in one transaction after generation completes successfully. Cancellation or a mid-stream
        failure persists nothing, so the conversation never shows a question without its answer.
        """
        with start_span(
            "answering.answer_stream",
            attributes={
                "user_id": str(owner_id),
                "conversation_id": str(conversation_id) if conversation_id else None,
            },
        ) as root:
            async for part in self._answer_stream(
                owner_id,
                question,
                conversation_id=conversation_id,
                document_ids=document_ids,
                collection_id=collection_id,
            ):
                if isinstance(part, AnswerResult):
                    set_span_attributes(
                        root,
                        {
                            "outcome": part.outcome.value,
                            "evidence": part.retrieval.evidence,
                            "citations": len(part.citations),
                            "streamed": True,
                        },
                    )
                yield part

    async def _answer_stream(  # noqa: PLR0915 - mirrors _answer(); streaming adds token yields
        self,
        owner_id: UUID,
        question: str,
        *,
        conversation_id: UUID | None = None,
        document_ids: Sequence[UUID] | None = None,
        collection_id: UUID | None = None,
    ) -> AsyncIterator[AnswerToken | AnswerResult]:
        started = time.perf_counter()
        state = await self._load_conversation(owner_id, question, conversation_id, collection_id)
        if document_ids is None and collection_id is None:
            collection_id = state.conversation.collection_id

        with start_span("answering.rewrite"):
            rewrite = await self._rewriter.rewrite(question, state.history)
        retrieval_started = time.perf_counter()
        retrieval = await self._retrieval.retrieve(
            owner_id, rewrite.query, document_ids=document_ids, collection_id=collection_id
        )
        retrieval_ms = _elapsed_ms(retrieval_started)

        generation_started = time.perf_counter()
        prompt: GroundedPrompt | None = None
        generation: Generation | None = None
        invalid = 0
        violation: str | None = None
        cited: tuple[int, ...] = ()

        if retrieval.context.is_empty:
            answer_text = INSUFFICIENT_EVIDENCE_STATEMENT
            outcome = AnswerOutcome.INSUFFICIENT_EVIDENCE
        else:
            prompt = self._prompt_builder.build(question, retrieval.context, history=state.history)
            try:
                async with asyncio.timeout(self._limits.generation_timeout_seconds):
                    with start_span(
                        "answering.generate",
                        attributes={
                            "llm.provider": self._llm.name,
                            "llm.model": self._llm.model,
                            "llm.stream": True,
                        },
                    ):
                        async for event in self._llm.generate_stream(
                            prompt.messages, options=self._options
                        ):
                            # Deltas are discarded until detect_violation passes (below).
                            if event.done is not None:
                                generation = event.done
            except TimeoutError as exc:
                raise GenerationTimeoutError from exc
            if generation is None:
                # Stream ended without a terminal event — treat as a provider fault; nothing
                # persisted (the client must discard any tokens already shown).
                raise LLMResponseInvalidError(
                    provider=self._llm.name,
                    model=self._llm.model,
                    diagnostics="stream ended without a complete generation",
                )
            cleaned = clean_references(generation.text, prompt.citation_indexes)
            invalid = cleaned.invalid_references
            violation = detect_violation(generation.text)
            if violation is not None:
                logger.warning(
                    "answer withheld",
                    extra={
                        "operation": "answer.blocked",
                        "user_id": str(owner_id),
                        "violation": violation,
                    },
                )
                answer_text = BLOCKED_ANSWER_STATEMENT
                outcome = AnswerOutcome.BLOCKED
                cited = ()
            elif is_insufficient(cleaned.text):
                answer_text = cleaned.text
                outcome = AnswerOutcome.INSUFFICIENT_EVIDENCE
                cited = ()
            else:
                answer_text = cleaned.text
                outcome = AnswerOutcome.ANSWERED
                cited = cleaned.cited
                # Safe to surface text only after the leak gate: emit the final answer as
                # deltas so SSE clients still receive progressive events without pre-check leaks.
                if answer_text:
                    yield AnswerToken(answer_text)
        generation_ms = _elapsed_ms(generation_started)

        persistence_started = time.perf_counter()
        with start_span(
            "answering.persist",
            attributes={"conversation_id": str(state.conversation.id)},
        ):
            user_message, assistant_message, citations = await self._persist(
                owner_id, state, question, answer_text, cited, retrieval
            )
        persistence_ms = _elapsed_ms(persistence_started)

        result = AnswerResult(
            outcome=outcome,
            answer=answer_text,
            citations=citations,
            conversation_id=state.conversation.id,
            user_message=user_message,
            assistant_message=assistant_message,
            retrieval=RetrievalMetadata(
                question=question,
                query=rewrite.query,
                rewritten=rewrite.applied,
                retriever=retrieval.retriever,
                documents_in_scope=retrieval.stats.documents_in_scope,
                hits=retrieval.stats.hits,
                evidence=len(retrieval.evidence),
                context_items=len(retrieval.context.items),
                context_characters=retrieval.context.characters,
                reranking=retrieval.reranking,
                prompt_version=prompt.version if prompt else None,
                model=generation.model if generation else None,
                finish_reason=generation.finish_reason if generation else None,
                invalid_references=invalid,
                violation=violation,
            ),
            usage=AnswerUsage(
                embeddings=retrieval.usage,
                generation=generation.usage if generation else LLMUsage(),
                rewriting=rewrite.usage,
            ),
            timing=AnswerTiming(
                rewrite_ms=rewrite.latency_ms,
                retrieval_ms=retrieval_ms,
                generation_ms=generation_ms,
                persistence_ms=persistence_ms,
                total_ms=_elapsed_ms(started),
            ),
        )
        logger.info(
            "question answered",
            extra={
                "operation": "answer.complete",
                "user_id": str(owner_id),
                "conversation_id": str(state.conversation.id),
                "outcome": outcome.value,
                "rewritten": rewrite.applied,
                "evidence": len(retrieval.evidence),
                "citations": len(citations),
                "invalid_references": invalid,
                "model": result.retrieval.model,
                "total_ms": result.timing.total_ms,
                "streamed": True,
            },
        )
        _emit_rag_metrics(result)
        yield result

    async def _load_conversation(
        self,
        owner_id: UUID,
        question: str,
        conversation_id: UUID | None,
        collection_id: UUID | None,
    ) -> _ConversationState:
        async with self._unit_of_work() as uow:
            if conversation_id is None:
                if collection_id is not None:
                    await ensure_collection_owned(uow, owner_id, collection_id)
                now = self._clock()
                conversation = Conversation(
                    id=self._new_id(),
                    owner_id=owner_id,
                    collection_id=collection_id,
                    title=title_from_text(question, max_length=MAX_CONVERSATION_TITLE_LENGTH),
                    created_at=now,
                    updated_at=now,
                )
                return _ConversationState(conversation, is_new=True, history=())
            existing = await uow.conversations.get(owner_id, conversation_id)
            if existing is None:
                raise ConversationNotFoundError
            # Only the newest turns are read: a conversation's length never grows the cost of
            # a question. One extra row is fetched so the ordering guard below always sees the
            # newest message even when no history is carried.
            messages = await uow.messages.list_for_conversation(
                owner_id, conversation_id, limit=self._limits.max_history_messages + 1
            )
        turns = [
            ChatMessage(message.role, message.content)
            for message in messages
            if message.role is not MessageRole.SYSTEM and message.content.strip()
        ]
        recent = _bounded_history(
            turns, self._limits.max_history_messages, self._limits.max_history_characters
        )
        last_at = max((message.created_at for message in messages), default=None)
        return _ConversationState(
            existing, is_new=False, history=tuple(recent), last_message_at=last_at
        )

    async def _persist(
        self,
        owner_id: UUID,
        state: _ConversationState,
        question: str,
        answer_text: str,
        cited: Sequence[int],
        retrieval: RetrievalResult,
    ) -> tuple[Message, Message, tuple[Citation, ...]]:
        # Strictly after the conversation's newest turn: repositories order messages by
        # created_at, and a coarse clock could otherwise interleave two quick exchanges.
        now = self._clock()
        if state.last_message_at is not None and now <= state.last_message_at:
            now = state.last_message_at + timedelta(microseconds=1)
        user_message = Message(
            id=self._new_id(),
            conversation_id=state.conversation.id,
            role=MessageRole.USER,
            content=question,
            created_at=now,
        )
        # Strictly after the user message, whatever the clock's resolution, so the two turns
        # always list in order (repositories order by created_at, then id).
        answered_at = max(self._clock(), now + timedelta(microseconds=1))
        assistant_message = Message(
            id=self._new_id(),
            conversation_id=state.conversation.id,
            role=MessageRole.ASSISTANT,
            content=answer_text,
            created_at=answered_at,
        )
        citations = build_citations(
            assistant_message.id,
            cited,
            retrieval.context,
            max_quote_characters=self._limits.max_quote_characters,
            new_id=self._new_id,
        )
        async with self._unit_of_work() as uow:
            await self._store_conversation(
                uow, owner_id, state, updated_at=assistant_message.created_at
            )
            await uow.messages.add(owner_id, user_message)
            await uow.messages.add(owner_id, assistant_message, citations)
            await uow.commit()
        return user_message, assistant_message, citations

    async def _store_conversation(
        self, uow: UnitOfWork, owner_id: UUID, state: _ConversationState, *, updated_at: datetime
    ) -> None:
        if state.is_new:
            await uow.conversations.add(state.conversation)
            return
        current = await uow.conversations.get(owner_id, state.conversation.id)
        if current is None:
            raise ConversationNotFoundError  # deleted while the answer was being produced
        await uow.conversations.update(replace(current, updated_at=updated_at))


def _emit_rag_metrics(result: AnswerResult) -> None:
    rag_dimensions = {"outcome": result.outcome.value}
    emit_count("RagAnswers", namespace=NAMESPACE_RAG, dimensions=rag_dimensions)
    emit_latency_ms(
        "RagTotalLatency",
        result.timing.total_ms,
        namespace=NAMESPACE_RAG,
        dimensions=rag_dimensions,
    )
    emit_latency_ms(
        "RagRewriteLatency",
        result.timing.rewrite_ms,
        namespace=NAMESPACE_RAG,
        dimensions=rag_dimensions,
    )
    emit_latency_ms(
        "RagRetrievalLatency",
        result.timing.retrieval_ms,
        namespace=NAMESPACE_RAG,
        dimensions=rag_dimensions,
    )
    emit_latency_ms(
        "RagGenerationLatency",
        result.timing.generation_ms,
        namespace=NAMESPACE_RAG,
        dimensions=rag_dimensions,
    )
    emit_latency_ms(
        "RagPersistenceLatency",
        result.timing.persistence_ms,
        namespace=NAMESPACE_RAG,
        dimensions=rag_dimensions,
    )
    emit_count(
        "RagEvidenceChunks",
        float(result.retrieval.evidence),
        namespace=NAMESPACE_RAG,
        dimensions=rag_dimensions,
    )
    if result.outcome is AnswerOutcome.BLOCKED:
        emit_count("RagBlockedAnswers", namespace=NAMESPACE_RAG, dimensions=rag_dimensions)
    if result.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE:
        emit_count(
            "RagInsufficientEvidence",
            namespace=NAMESPACE_RAG,
            dimensions=rag_dimensions,
        )
    if result.usage.generation.input_tokens:
        emit_count(
            "AiInputTokens",
            float(result.usage.generation.input_tokens),
            namespace=NAMESPACE_AI,
            dimensions={"provider": "llm", "kind": "llm"},
        )
    if result.usage.generation.output_tokens:
        emit_count(
            "AiOutputTokens",
            float(result.usage.generation.output_tokens),
            namespace=NAMESPACE_AI,
            dimensions={"provider": "llm", "kind": "llm"},
        )


def _bounded_history(
    turns: Sequence[ChatMessage], max_messages: int, max_characters: int
) -> tuple[ChatMessage, ...]:
    """The newest turns within both limits, oldest dropped first (the prompt builder applies
    the same rule again, so the rewriter and the prompt see the same window)."""
    kept: list[ChatMessage] = []
    characters = 0
    for turn in reversed(turns):
        if len(kept) >= max_messages or characters + len(turn.content) > max_characters:
            break
        kept.append(turn)
        characters += len(turn.content)
    kept.reverse()
    return tuple(kept)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = [
    "AnswerLimits",
    "AnswerService",
    "LLMQueryRewriter",
    "NoQueryRewriting",
    "QueryRewriter",
    "Rewrite",
]
