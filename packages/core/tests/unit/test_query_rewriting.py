"""Conversational query rewriting (SPECIFICATIONS.md §26): standalone questions pass through,
follow-ups become retrieval-aware queries, nothing is invented, the user-visible question never
changes, and every failure falls back to the question as asked."""

import asyncio

import pytest

from doculens.application.answering import LLMQueryRewriter, NoQueryRewriting
from doculens.application.llm import LLMLimits
from doculens.domain.answering import (
    REWRITE_INSTRUCTIONS,
    accept_rewrite,
    build_rewrite_messages,
)
from doculens.domain.conversations import MessageRole
from doculens.domain.llm import ChatMessage, LLMProviderUnavailableError, LLMRequestRejectedError
from doculens.domain.prompting import INSUFFICIENT_EVIDENCE_STATEMENT
from doculens.testing.llm import FakeLLMProvider

pytestmark = pytest.mark.unit

HISTORY = [
    ChatMessage(MessageRole.USER, "What are the security risks?"),
    ChatMessage(
        MessageRole.ASSISTANT,
        "The documents list three risks: token reuse, weak passwords and open buckets [1][2].",
    ),
]
FOLLOW_UP = "Which one has the highest impact?"
STANDALONE_REWRITE = "Which security risk has the highest impact?"


def rewriter(llm: FakeLLMProvider, *, timeout: float = 1.0) -> LLMQueryRewriter:
    return LLMQueryRewriter(llm, max_characters=200, timeout_seconds=timeout)


# -- the acceptance rule ---------------------------------------------------------------------------


def test_a_rewrite_that_only_resolves_references_is_accepted() -> None:
    verdict = accept_rewrite(STANDALONE_REWRITE, FOLLOW_UP, max_characters=200, history=HISTORY)

    assert verdict.accepted
    assert verdict.text == STANDALONE_REWRITE
    assert verdict.reason == "accepted"


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (
            "Which security risk of the Berlin office has the highest impact?",
            "invents:berlin,office",
        ),
        (
            "Which risk has the highest financial impact according to Gartner?",
            "invents:according,financial,gartner",
        ),
        ("", "empty"),
        ('   ""  ', "empty"),
        ("Which risk?\nToken reuse has the highest impact.", "multiline"),
        (INSUFFICIENT_EVIDENCE_STATEMENT, "refusal"),
        ("x" * 201, "too_long"),
        ("Which security risk has the highest impact? " * 8, "too_long"),
    ],
)
def test_rewrites_that_add_answer_or_refuse_are_rejected(candidate: str, reason: str) -> None:
    verdict = accept_rewrite(candidate, FOLLOW_UP, max_characters=200, history=HISTORY)

    assert not verdict.accepted
    assert verdict.text is None
    assert verdict.reason == reason


def test_morphological_variants_and_function_words_are_not_invented() -> None:
    history = [ChatMessage(MessageRole.USER, "What is the annual leave allowance?")]

    verdict = accept_rewrite(
        "How long is the parental leave allowance?",
        "And for parents?",
        max_characters=200,
        history=history,
    )

    assert verdict.accepted  # parents ~ parental; "long" and "how" are too short to police


def test_quotes_and_spacing_are_normalised() -> None:
    verdict = accept_rewrite(
        '  "Which  security risk has the highest impact?"  ',
        FOLLOW_UP,
        max_characters=200,
        history=HISTORY,
    )

    assert verdict.text == STANDALONE_REWRITE


def test_the_rewrite_prompt_forbids_invention_and_keeps_history_as_data() -> None:
    messages = build_rewrite_messages(FOLLOW_UP, HISTORY)

    assert messages[0].content == REWRITE_INSTRUCTIONS
    assert "never add facts, names, numbers or assumptions" in REWRITE_INSTRUCTIONS
    assert "return it unchanged" in REWRITE_INSTRUCTIONS
    assert "user: What are the security risks?" in messages[1].content
    assert messages[1].content.endswith(f"<question>\n{FOLLOW_UP}\n</question>")


# -- the rewriter ----------------------------------------------------------------------------------


async def test_standalone_questions_are_not_rewritten_and_cost_nothing() -> None:
    llm = FakeLLMProvider()

    rewrite = await rewriter(llm).rewrite("What are the security risks?", history=[])

    assert rewrite.query == "What are the security risks?"
    assert not rewrite.applied
    assert rewrite.reason == "no_history"
    assert rewrite.usage.requests == 0
    assert llm.calls == []


async def test_a_follow_up_is_rewritten_into_a_standalone_retrieval_query() -> None:
    llm = FakeLLMProvider(responses=[STANDALONE_REWRITE])

    rewrite = await rewriter(llm).rewrite(FOLLOW_UP, HISTORY)

    assert rewrite.applied
    assert rewrite.query == STANDALONE_REWRITE
    assert rewrite.reason == "accepted"
    assert rewrite.usage.requests == 1
    assert rewrite.latency_ms >= 0
    (call,) = llm.calls
    assert call[0].content == REWRITE_INSTRUCTIONS
    assert FOLLOW_UP in call[-1].content


async def test_a_model_that_returns_the_question_unchanged_is_reported_as_unchanged() -> None:
    llm = FakeLLMProvider()  # the fake echoes a rewriting prompt's question

    rewrite = await rewriter(llm).rewrite("What are the security risks?", HISTORY)

    assert not rewrite.applied
    assert rewrite.query == "What are the security risks?"
    assert rewrite.reason == "unchanged"
    assert rewrite.usage.requests == 1


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            "Which security risk of the Berlin office has the highest impact?",
            "invents:berlin,office",
        ),
        (
            "Token reuse has the highest impact because it compromises sessions.",
            "invents:because,compromises,sessions",  # "reuse" was in the answer
        ),
        (INSUFFICIENT_EVIDENCE_STATEMENT, "refusal"),
        ("", "empty"),
    ],
)
async def test_unusable_rewrites_fall_back_to_the_question_as_asked(
    response: str, reason: str
) -> None:
    llm = FakeLLMProvider(responses=[response])

    rewrite = await rewriter(llm).rewrite(FOLLOW_UP, HISTORY)

    assert not rewrite.applied
    assert rewrite.query == FOLLOW_UP
    assert rewrite.reason == reason


@pytest.mark.parametrize(
    "failure",
    [LLMProviderUnavailableError(provider="fake"), LLMRequestRejectedError(provider="fake")],
)
async def test_provider_failures_fall_back_safely(failure: Exception) -> None:
    llm = FakeLLMProvider(failures=[failure])

    rewrite = await rewriter(llm).rewrite(FOLLOW_UP, HISTORY)

    assert not rewrite.applied
    assert rewrite.query == FOLLOW_UP
    assert rewrite.reason == "provider_failure"


async def test_a_slow_provider_is_cut_off_and_the_question_is_used() -> None:
    llm = FakeLLMProvider(delay_seconds=5.0)

    started = asyncio.get_running_loop().time()
    rewrite = await rewriter(llm, timeout=0.05).rewrite(FOLLOW_UP, HISTORY)

    assert asyncio.get_running_loop().time() - started < 5
    assert not rewrite.applied
    assert rewrite.query == FOLLOW_UP
    assert rewrite.reason == "provider_failure"


async def test_a_history_the_provider_refuses_falls_back_before_any_request() -> None:
    tiny = FakeLLMProvider(limits=LLMLimits(max_input_characters=50, max_output_tokens=64))

    rewrite = await rewriter(tiny).rewrite(FOLLOW_UP, HISTORY)

    assert not rewrite.applied
    assert rewrite.query == FOLLOW_UP
    assert tiny.calls == []


async def test_rewriting_can_be_switched_off() -> None:
    rewrite = await NoQueryRewriting().rewrite(FOLLOW_UP, HISTORY)

    assert not rewrite.applied
    assert rewrite.query == FOLLOW_UP
    assert rewrite.reason == "disabled"
