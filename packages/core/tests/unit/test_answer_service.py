"""Question → retrieval → prompt → LLM → answer → citations → persistence, with fakes for the
vector store, embeddings, repositories and the language model."""

import asyncio
from uuid import uuid4

import pytest

from doculens.application.answering import (
    AnswerLimits,
    AnswerService,
    LLMQueryRewriter,
    NoQueryRewriting,
)
from doculens.application.llm import LLMLimits
from doculens.application.rag import RagQuery, RagService
from doculens.application.reranking import RerankingStage
from doculens.application.retrieval import RetrievalService, build_retriever
from doculens.domain.answering import AnswerOutcome, GenerationTimeoutError
from doculens.domain.conversations import ConversationNotFoundError, MessageRole
from doculens.domain.errors import ConflictError
from doculens.domain.llm import GenerationOptions, LLMProviderUnavailableError
from doculens.domain.prompting import (
    INSUFFICIENT_EVIDENCE_STATEMENT,
    SYSTEM_INSTRUCTIONS,
    PromptBuilder,
)
from doculens.domain.reranking import RerankingLimits, RerankingStatus
from doculens.domain.retrieval import ContextLimits, RetrievalLimits, RetrievalStrategy
from doculens.testing.factories import Factories
from doculens.testing.llm import FakeLLMProvider
from doculens.testing.reranking import FakeReranker
from doculens.testing.retrieval import IndexedCorpus
from doculens.testing.vectors import InMemoryVectorStore

pytestmark = pytest.mark.unit

TEXTS = [
    "Refresh tokens rotate on every use and reuse revokes the session family.",
    "Employees receive twenty-five days of annual leave.",
    "Parental leave is sixteen weeks.",
]


class World(IndexedCorpus[InMemoryVectorStore]):
    def __init__(self, *, reranker: FakeReranker | None = None) -> None:
        super().__init__(InMemoryVectorStore())
        self.llm = FakeLLMProvider()
        self.retrieval = RetrievalService(
            unit_of_work=self.unit_of_work,
            retriever=build_retriever(
                RetrievalStrategy.HYBRID,
                unit_of_work=self.unit_of_work,
                embeddings=self.embeddings,
                vectors=self.vectors,
                min_score=-1.0,
            ),
            limits=RetrievalLimits(max_query_characters=200, candidate_limit=10),
            context_limits=ContextLimits(max_chunks=3, max_characters=5_000),
            reranking=RerankingStage(reranker, limits=RerankingLimits(top_k=2, timeout_seconds=1))
            if reranker
            else None,
        )

    def service(
        self,
        *,
        rewriter: LLMQueryRewriter | NoQueryRewriting | None = None,
        options: GenerationOptions | None = None,
        limits: AnswerLimits | None = None,
    ) -> AnswerService:
        return AnswerService(
            unit_of_work=self.unit_of_work,
            retrieval=self.retrieval,
            prompt_builder=PromptBuilder(),
            llm=self.llm,
            rewriter=rewriter,
            options=options,
            limits=limits,
        )


@pytest.fixture
async def world() -> World:
    world = World()
    await world.index(TEXTS, filename="handbook.pdf")
    return world


async def test_a_question_is_answered_cited_and_persisted(world: World) -> None:
    result = await world.service().answer(world.owner.id, "How do refresh tokens rotate?")

    assert result.outcome is AnswerOutcome.ANSWERED
    assert result.grounded
    assert result.answer == "Fake answer to: How do refresh tokens rotate? [1]"
    (citation,) = result.citations  # [1] is the best-ranked chunk of the prompt
    assert citation.quoted_text == TEXTS[0]
    assert citation.page_number == 1
    assert citation.citation_order == 0
    assert citation.message_id == result.assistant_message_id
    assert citation.chunk_id is not None
    assert citation.reranking_score is None
    # Persisted: a new conversation titled after the question, both messages, the citation.
    conversation = world.store.conversations[result.conversation_id]
    assert conversation.owner_id == world.owner.id
    assert conversation.title == "How do refresh tokens rotate?"
    messages = [m for m in world.store.messages.values() if m.conversation_id == conversation.id]
    assert [(m.role, m.content) for m in sorted(messages, key=lambda m: m.created_at)] == [
        (MessageRole.USER, "How do refresh tokens rotate?"),
        (MessageRole.ASSISTANT, result.answer),
    ]
    assert world.store.citations[citation.id] == citation
    # Retrieval, usage and timing metadata.
    assert result.retrieval.question == result.retrieval.query == "How do refresh tokens rotate?"
    assert not result.retrieval.rewritten
    assert result.retrieval.retriever == "hybrid"
    assert result.retrieval.documents_in_scope == 1
    assert result.retrieval.evidence == 3
    assert result.retrieval.context_items == 3
    assert result.retrieval.prompt_version == "grounded-v1"
    assert result.retrieval.model == "fake-llm-v1"
    assert result.retrieval.invalid_references == 0
    assert result.retrieval.reranking.status is RerankingStatus.DISABLED
    assert result.usage.embeddings.requests == 1
    assert result.usage.generation.requests == 1
    assert result.usage.rewriting.requests == 0
    assert result.usage.llm.requests == 1
    assert result.timing.total_ms >= max(
        result.timing.retrieval_ms, result.timing.generation_ms, result.timing.persistence_ms
    )
    assert not result.truncated
    # The model saw the grounded prompt: policy first, documents and the question last.
    (call,) = world.llm.calls
    assert call[0].content == SYSTEM_INSTRUCTIONS
    assert "<retrieved_documents>" in call[-1].content
    assert TEXTS[0] in call[-1].content
    assert call[-1].content.endswith("<question>\nHow do refresh tokens rotate?\n</question>")


async def test_without_evidence_no_model_is_called_and_nothing_is_fabricated(world: World) -> None:
    result = await world.service().answer(world.stranger.id, "How do refresh tokens rotate?")

    assert result.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert not result.grounded
    assert result.answer == INSUFFICIENT_EVIDENCE_STATEMENT
    assert result.citations == ()
    assert world.llm.calls == []
    assert result.usage.generation.requests == 0
    assert result.retrieval.model is None
    assert result.retrieval.prompt_version is None
    # The exchange is still part of the conversation.
    assistant = world.store.messages[result.assistant_message_id]
    assert assistant.content == INSUFFICIENT_EVIDENCE_STATEMENT
    assert result.conversation_id in world.store.conversations


async def test_a_model_that_reports_insufficient_evidence_is_not_presented_as_grounded(
    world: World,
) -> None:
    world.llm.responses = [f"{INSUFFICIENT_EVIDENCE_STATEMENT} The documents cover leave [2]."]

    result = await world.service().answer(world.owner.id, "What is the CEO's salary?")

    assert result.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert result.answer.startswith(INSUFFICIENT_EVIDENCE_STATEMENT)
    assert result.citations == ()
    assert len(world.llm.calls) == 1


async def test_invalid_references_are_dropped_and_counted(world: World) -> None:
    world.llm.responses = [
        "Leave is twenty-five days [2]. Parents get sixteen weeks [3][9]. See [1, 7]."
    ]

    result = await world.service().answer(world.owner.id, "What leave do employees get?")

    assert result.outcome is AnswerOutcome.ANSWERED
    assert result.answer == "Leave is twenty-five days [2]. Parents get sixteen weeks [3]. See [1]."
    assert [c.citation_order for c in result.citations] == [0, 1, 2]
    assert result.retrieval.invalid_references == 2
    assert len({c.chunk_id for c in result.citations}) == 3


async def test_follow_up_questions_are_rewritten_for_retrieval_but_stored_as_asked(
    world: World,
) -> None:
    service = world.service(
        rewriter=LLMQueryRewriter(world.llm, max_characters=200, timeout_seconds=1)
    )
    first = await service.answer(world.owner.id, "What is the annual leave allowance?")
    world.llm.responses = ["What is the parental leave allowance?", "Sixteen weeks [1]."]

    second = await service.answer(
        world.owner.id, "And for parents?", conversation_id=first.conversation_id
    )

    assert second.conversation_id == first.conversation_id
    assert second.retrieval.rewritten
    assert second.retrieval.question == "And for parents?"
    assert second.retrieval.query == "What is the parental leave allowance?"
    assert second.usage.rewriting.requests == 1
    assert second.usage.llm.requests == 2
    assert second.timing.rewrite_ms >= 0
    assert world.store.messages[second.user_message_id].content == "And for parents?"
    assert second.answer == "Sixteen weeks [1]."
    assert second.citations[0].quoted_text == "Parental leave is sixteen weeks."
    # The rewrite call saw the history; the answer call carried it as separate turns.
    rewrite_call, answer_call = world.llm.calls[1:]
    assert "user: What is the annual leave allowance?" in rewrite_call[-1].content
    assert [m.role for m in answer_call] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    assert answer_call[1].content == "What is the annual leave allowance?"
    assert answer_call[2].content == first.answer
    conversation = world.store.conversations[first.conversation_id]
    assert conversation.updated_at >= conversation.created_at


async def test_rewriting_is_skipped_without_history_and_fails_open(world: World) -> None:
    rewriter = LLMQueryRewriter(world.llm, max_characters=200, timeout_seconds=1)
    service = world.service(rewriter=rewriter)

    first = await service.answer(world.owner.id, "What is the annual leave allowance?")
    assert not first.retrieval.rewritten
    assert len(world.llm.calls) == 1  # no rewrite call without history

    world.llm.failures = [LLMProviderUnavailableError(provider="fake")]
    second = await service.answer(
        world.owner.id, "And for parents?", conversation_id=first.conversation_id
    )
    assert second.outcome is AnswerOutcome.ANSWERED
    assert not second.retrieval.rewritten
    assert second.retrieval.query == "And for parents?"

    slow = LLMQueryRewriter(
        FakeLLMProvider(delay_seconds=5.0), max_characters=200, timeout_seconds=0.05
    )
    third = await world.service(rewriter=slow).answer(
        world.owner.id, "And remote work?", conversation_id=first.conversation_id
    )
    assert not third.retrieval.rewritten
    assert third.outcome is AnswerOutcome.ANSWERED


async def test_a_generation_failure_propagates_and_persists_nothing(world: World) -> None:
    world.llm.failures = [LLMProviderUnavailableError(provider="fake")]

    with pytest.raises(LLMProviderUnavailableError):
        await world.service().answer(world.owner.id, "How do refresh tokens rotate?")

    assert world.store.messages == {}
    assert world.store.citations == {}
    assert world.store.conversations == {}


async def test_unknown_or_foreign_conversations_are_not_found(world: World) -> None:
    theirs = Factories.conversation(world.stranger.id)
    world.store.conversations[theirs.id] = theirs

    with pytest.raises(ConversationNotFoundError):
        await world.service().answer(world.owner.id, "q", conversation_id=theirs.id)
    with pytest.raises(ConversationNotFoundError):
        await world.service().answer(world.owner.id, "q", conversation_id=uuid4())
    assert world.store.messages == {}


async def test_the_conversation_collection_scopes_retrieval_unless_overridden(
    world: World,
) -> None:
    cookbook = await world.index(["Proof the dough for twelve hours."], filename="cookbook.pdf")
    conversation = Factories.conversation(world.owner.id, world.collection.id)
    world.store.conversations[conversation.id] = conversation
    await world.index(
        ["Deploy with Terraform."], collection=world.collection.id, filename="ops.pdf"
    )

    scoped = await world.service().answer(
        world.owner.id, "How do we deploy?", conversation_id=conversation.id
    )
    assert scoped.retrieval.documents_in_scope == 1
    assert scoped.citations[0].quoted_text == "Deploy with Terraform."

    overridden = await world.service().answer(
        world.owner.id,
        "How long to proof?",
        conversation_id=conversation.id,
        document_ids=[cookbook.id],
    )
    assert overridden.retrieval.documents_in_scope == 1
    assert overridden.citations[0].document_id == cookbook.id

    fresh = await world.service().answer(
        world.owner.id, "How do we deploy?", collection_id=world.collection.id
    )
    assert world.store.conversations[fresh.conversation_id].collection_id == world.collection.id


async def test_reranked_evidence_is_cited_with_both_scores() -> None:
    reranked = World(reranker=FakeReranker())
    await reranked.index(TEXTS, filename="handbook.pdf")

    result = await reranked.service().answer(reranked.owner.id, "What is the parental leave?")

    assert result.retrieval.reranking.status is RerankingStatus.APPLIED
    assert result.retrieval.context_items == 2
    (citation,) = result.citations
    assert citation.quoted_text == "Parental leave is sixteen weeks."
    assert citation.reranking_score is not None
    assert citation.retrieval_score != citation.reranking_score


async def test_truncated_answers_and_quote_limits_are_reported(world: World) -> None:
    world.llm.responses = ["A very long answer " * 20 + "[1]"]

    result = await world.service(
        options=GenerationOptions(max_output_tokens=5),
        limits=AnswerLimits(max_quote_characters=12),
    ).answer(world.owner.id, "How do refresh tokens rotate?")

    assert result.truncated
    assert result.retrieval.finish_reason is not None
    assert result.citations == ()  # the marker was cut off with the text
    assert result.outcome is AnswerOutcome.ANSWERED

    world.llm.responses = ["Short [1]."]
    cited = await world.service(limits=AnswerLimits(max_quote_characters=12)).answer(
        world.owner.id, "How do refresh tokens rotate?"
    )
    assert cited.citations[0].quoted_text == TEXTS[0][:12]


async def test_the_rag_boundary_answers_through_the_same_pipeline(world: World) -> None:
    rag = RagService(retrieval=world.retrieval, answering=world.service())

    result = await rag.answer(
        RagQuery(owner_id=world.owner.id, question="How do refresh tokens rotate?")
    )
    follow_up = await rag.answer(
        RagQuery(owner_id=world.owner.id, question="And the annual leave?"),
        conversation_id=result.conversation_id,
    )

    assert result.grounded
    assert follow_up.conversation_id == result.conversation_id
    assert len(world.store.messages) == 4


async def test_generation_is_bounded_by_the_per_question_budget(world: World) -> None:
    world.llm.delay_seconds = 5.0
    service = world.service(limits=AnswerLimits(generation_timeout_seconds=0.05))

    started = asyncio.get_running_loop().time()
    with pytest.raises(GenerationTimeoutError):
        await service.answer(world.owner.id, "How do refresh tokens rotate?")

    assert asyncio.get_running_loop().time() - started < 5
    assert world.store.messages == {}  # nothing persisted, the client may retry
    with pytest.raises(ValueError, match="timeout"):
        AnswerLimits(generation_timeout_seconds=0)


async def test_rewriting_fails_open_when_the_provider_refuses_the_history(world: World) -> None:
    tiny = FakeLLMProvider(limits=LLMLimits(max_input_characters=40, max_output_tokens=64))
    service = world.service(rewriter=LLMQueryRewriter(tiny, max_characters=200, timeout_seconds=1))
    first = await service.answer(world.owner.id, "What is the annual leave allowance?")

    second = await service.answer(
        world.owner.id, "And for parents?", conversation_id=first.conversation_id
    )

    assert second.outcome is AnswerOutcome.ANSWERED
    assert not second.retrieval.rewritten
    assert tiny.calls == []  # refused before any request
    assert second.retrieval.query == "And for parents?"


async def test_uncited_answers_are_flagged_for_the_caller(world: World) -> None:
    world.llm.responses = ["Revenue grew in [2024] and costs stayed flat."]

    result = await world.service().answer(world.owner.id, "How did revenue do?")

    assert result.outcome is AnswerOutcome.ANSWERED
    assert result.uncited
    assert result.citations == ()
    assert (
        result.answer == "Revenue grew in [2024] and costs stayed flat."
    )  # a year, not a reference
    assert result.retrieval.invalid_references == 0


async def test_history_is_bounded_in_count_and_size_for_the_rewriter_and_the_prompt(
    world: World,
) -> None:
    rewriter = LLMQueryRewriter(world.llm, max_characters=200, timeout_seconds=1)
    service = world.service(
        rewriter=rewriter,
        limits=AnswerLimits(max_history_messages=4, max_history_characters=120),
    )
    first = await service.answer(world.owner.id, "What is the annual leave allowance?")
    conversation = first.conversation_id
    for question in ("How much remote work?", "What about tokens?", "And parental leave?"):
        await service.answer(world.owner.id, question, conversation_id=conversation)
    world.llm.calls.clear()

    await service.answer(world.owner.id, "And for parents?", conversation_id=conversation)

    rewrite_call, answer_call = world.llm.calls
    carried = list(answer_call[1:-1])
    assert len(carried) <= 4
    assert sum(len(m.content) for m in carried) <= 120
    assert carried[-1].role is MessageRole.ASSISTANT  # the newest turns, not the oldest
    assert "What is the annual leave allowance?" not in rewrite_call[-1].content
    assert "And parental leave?" in rewrite_call[-1].content


async def test_the_ordering_guard_holds_even_when_no_history_is_carried(world: World) -> None:
    service = world.service(limits=AnswerLimits(max_history_messages=0))
    first = await service.answer(world.owner.id, "What is the annual leave allowance?")

    second = await service.answer(
        world.owner.id, "And for parents?", conversation_id=first.conversation_id
    )

    assert second.user_message.created_at > first.assistant_message.created_at
    assert [m.role for m in world.llm.calls[-1]] == [MessageRole.SYSTEM, MessageRole.USER]


async def test_concurrent_questions_in_one_conversation_keep_each_exchange_contiguous(
    world: World,
) -> None:
    service = world.service()
    first = await service.answer(world.owner.id, "What is the annual leave allowance?")
    conversation = first.conversation_id

    results = await asyncio.gather(
        *(
            service.answer(world.owner.id, f"Question {i}?", conversation_id=conversation)
            for i in range(5)
        )
    )

    history = sorted(
        (m for m in world.store.messages.values() if m.conversation_id == conversation),
        key=lambda m: (m.created_at, m.id.hex),
    )
    assert len(history) == 12
    assert len({m.id for m in history}) == 12  # no duplicates
    assert [m.role for m in history] == [MessageRole.USER, MessageRole.ASSISTANT] * 6
    for result in results:
        index = next(i for i, m in enumerate(history) if m.id == result.user_message_id)
        assert history[index + 1].id == result.assistant_message_id
    assert len({m.created_at for m in history}) == 12  # strictly ordered timestamps


async def test_a_conversation_deleted_while_answering_is_reported_not_recorded(
    world: World,
) -> None:
    first = await world.service().answer(world.owner.id, "What is the annual leave allowance?")

    class DeletingLLM(FakeLLMProvider):
        async def generate(self, messages, *, options=None):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202
            del world.store.conversations[first.conversation_id]
            return await super().generate(messages, options=options)

    world.llm = DeletingLLM()
    with pytest.raises(ConversationNotFoundError):
        await world.service().answer(
            world.owner.id, "And for parents?", conversation_id=first.conversation_id
        )

    assert len(world.store.messages) == 2  # only the first exchange remains


async def test_duplicate_message_identifiers_are_refused_by_the_store(world: World) -> None:
    result = await world.service().answer(world.owner.id, "What is the annual leave allowance?")

    async with world.unit_of_work() as uow:
        with pytest.raises(ConflictError):
            await uow.messages.add(world.owner.id, result.user_message)
