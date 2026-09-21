"""Pure answering rules: reference validation, insufficient-evidence detection, citations,
the rewriting prompt and its acceptance rule."""

from uuid import UUID, uuid4

import pytest

from doculens.domain.answering import (
    REWRITE_INSTRUCTIONS,
    AnswerOutcome,
    AnswerResult,
    AnswerTiming,
    AnswerUsage,
    RetrievalMetadata,
    accept_rewrite,
    build_citations,
    build_rewrite_messages,
    clean_references,
    is_insufficient,
    question_of_rewrite_prompt,
)
from doculens.domain.conversations import MessageRole
from doculens.domain.llm import ChatMessage, FinishReason, LLMUsage
from doculens.domain.prompting import INSUFFICIENT_EVIDENCE_STATEMENT
from doculens.domain.reranking import RerankingReport, RerankingStatus
from doculens.domain.retrieval import AssembledContext, ContextItem, Evidence

pytestmark = pytest.mark.unit


def _evidence(index: int, text: str, *, reranked: bool = False) -> Evidence:
    metadata: dict[str, object] = {"chunk_index": index, "rank": index, "filename": "f.pdf"}
    if reranked:
        metadata["retrieval_score"] = 0.42
    return Evidence(
        document_id=uuid4(),
        chunk_id=uuid4(),
        page_number=index + 10,
        text=text,
        score=0.9 - index / 10,
        metadata=metadata,
    )


def _context(*evidence: Evidence) -> AssembledContext:
    items = tuple(ContextItem(index=i + 1, evidence=e) for i, e in enumerate(evidence))
    return AssembledContext(items=items, characters=sum(len(e.text) for e in evidence), omitted=0)


def test_references_are_validated_against_the_context_and_spacing_tidied() -> None:
    cleaned = clean_references(
        "Revenue grew [1]. Costs were flat [2][7] . Both matter [1, 3] and [9].",
        frozenset({1, 2, 3}),
    )

    assert cleaned.text == "Revenue grew [1]. Costs were flat [2]. Both matter [1][3] and."
    assert cleaned.cited == (1, 2, 3)
    assert cleaned.invalid_references == 2


def test_bracketed_figures_are_not_references() -> None:
    cleaned = clean_references("Revenue grew in [2024] by [12] percent [1].", frozenset({1}))

    assert cleaned.text == "Revenue grew in [2024] by percent [1]."
    assert cleaned.cited == (1,)
    assert cleaned.invalid_references == 1


def test_an_answer_without_references_is_kept_verbatim() -> None:
    cleaned = clean_references("No references here.", frozenset({1}))

    assert cleaned.text == "No references here."
    assert cleaned.cited == ()
    assert cleaned.invalid_references == 0


def test_the_insufficient_evidence_statement_is_recognised_robustly() -> None:
    assert is_insufficient(INSUFFICIENT_EVIDENCE_STATEMENT)
    assert is_insufficient(
        f"  {INSUFFICIENT_EVIDENCE_STATEMENT.upper()} The documents cover leave."
    )
    assert is_insufficient(
        "the available documents  do not contain\nenough information to answer this question."
    )
    assert not is_insufficient("Revenue grew twelve percent [1].")
    assert not is_insufficient("The available documents say revenue grew.")


def test_citations_quote_the_evidence_in_reference_order() -> None:
    first = _evidence(0, "x" * 600)
    second = _evidence(1, "second passage", reranked=True)
    ids = iter([UUID(int=1), UUID(int=2)])
    message = UUID(int=99)

    citations = build_citations(
        message, (2, 1), _context(first, second), max_quote_characters=500, new_id=lambda: next(ids)
    )

    assert [c.id for c in citations] == [UUID(int=1), UUID(int=2)]
    assert [c.citation_order for c in citations] == [0, 1]
    assert all(c.message_id == message for c in citations)
    reranked, plain = citations
    assert reranked.document_id == second.document_id
    assert reranked.chunk_id == second.chunk_id
    assert reranked.page_number == 11
    assert reranked.quoted_text == "second passage"
    assert reranked.retrieval_score == 0.42
    assert reranked.reranking_score == second.score
    assert plain.quoted_text == "x" * 500
    assert plain.retrieval_score == first.score
    assert plain.reranking_score is None


def test_the_rewrite_prompt_carries_history_as_data_and_the_question_last() -> None:
    history = [
        ChatMessage(MessageRole.USER, "What is the  leave policy?"),
        ChatMessage(MessageRole.ASSISTANT, "Twenty-five days [1]."),
    ]

    messages = build_rewrite_messages("And for parents?", history)

    assert [m.role for m in messages] == [MessageRole.SYSTEM, MessageRole.USER]
    assert messages[0].content == REWRITE_INSTRUCTIONS
    assert "user: What is the leave policy?" in messages[1].content
    assert "assistant: Twenty-five days [1]." in messages[1].content
    assert messages[1].content.endswith("<question>\nAnd for parents?\n</question>")
    assert question_of_rewrite_prompt(messages) == "And for parents?"
    assert question_of_rewrite_prompt([ChatMessage(MessageRole.USER, "plain")]) is None
    assert question_of_rewrite_prompt([]) is None


@pytest.mark.parametrize(
    ("candidate", "accepted"),
    [
        ("What is the parental leave allowance?", "What is the parental leave allowance?"),
        ('"What is the parental leave allowance?"', "What is the parental leave allowance?"),
        ("  spaced   out  ", "spaced out"),
        ("", None),
        ("x" * 201, None),
        (INSUFFICIENT_EVIDENCE_STATEMENT, None),
        ("Here is a long answer instead of a question. " * 6, None),
    ],
)
def test_rewrites_are_accepted_only_when_plausible(candidate: str, accepted: str | None) -> None:
    assert accept_rewrite(candidate, "And for parents?", max_characters=200) == accepted


def test_the_result_reports_grounding_and_truncation() -> None:
    def metadata(finish: FinishReason | None) -> RetrievalMetadata:
        return RetrievalMetadata(
            question="q",
            query="q",
            rewritten=False,
            retriever="hybrid",
            documents_in_scope=1,
            hits=1,
            evidence=1,
            context_items=1,
            context_characters=10,
            reranking=RerankingReport(status=RerankingStatus.DISABLED),
            prompt_version="grounded-v1",
            model="m",
            finish_reason=finish,
            invalid_references=0,
        )

    def result(outcome: AnswerOutcome, finish: FinishReason | None) -> AnswerResult:
        return AnswerResult(
            outcome=outcome,
            answer="a",
            citations=(),
            conversation_id=uuid4(),
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
            retrieval=metadata(finish),
            usage=AnswerUsage(
                generation=LLMUsage(requests=1, output_tokens=3),
                rewriting=LLMUsage(requests=1, output_tokens=2),
            ),
            timing=AnswerTiming(total_ms=5),
        )

    assert result(AnswerOutcome.ANSWERED, FinishReason.STOP).grounded
    assert not result(AnswerOutcome.INSUFFICIENT_EVIDENCE, None).grounded
    assert result(AnswerOutcome.ANSWERED, FinishReason.LENGTH).truncated
    assert not result(AnswerOutcome.ANSWERED, FinishReason.STOP).truncated
    assert result(AnswerOutcome.ANSWERED, FinishReason.STOP).usage.llm.output_tokens == 5
    assert result(AnswerOutcome.ANSWERED, FinishReason.STOP).usage.llm.requests == 2
