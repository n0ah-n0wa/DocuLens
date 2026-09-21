"""Vendor-free language-model concepts: prompt validation, usage arithmetic, errors."""

import pytest

from doculens.application.llm import LLMLimits
from doculens.domain.conversations import MessageRole
from doculens.domain.errors import DependencyUnavailableError, DomainError, InvalidInputError
from doculens.domain.llm import (
    ChatMessage,
    FinishReason,
    Generation,
    GenerationOptions,
    LLMError,
    LLMInputError,
    LLMProviderUnavailableError,
    LLMRateLimitedError,
    LLMRequestRejectedError,
    LLMResponseInvalidError,
    LLMUsage,
    validate_messages,
)

pytestmark = pytest.mark.unit

SYSTEM = ChatMessage(MessageRole.SYSTEM, "Answer only from the evidence.")
USER = ChatMessage(MessageRole.USER, "What grew?")
ASSISTANT = ChatMessage(MessageRole.ASSISTANT, "Revenue grew.")


def test_a_well_formed_prompt_passes() -> None:
    validate_messages([SYSTEM, USER, ASSISTANT, USER], max_input_characters=1000)
    validate_messages([USER], max_input_characters=1000)


@pytest.mark.parametrize(
    ("messages", "problem"),
    [
        ([], "no messages"),
        ([ChatMessage(MessageRole.USER, "   ")], "empty"),
        ([USER, SYSTEM, USER], "only the first message may be the system"),
        ([SYSTEM, USER, ASSISTANT], "end with the user"),
        ([SYSTEM], "end with the user"),
        ([ChatMessage(MessageRole.USER, "x" * 11)], "exceeds 10"),
    ],
)
def test_malformed_prompts_are_refused_before_any_request(
    messages: list[ChatMessage], problem: str
) -> None:
    with pytest.raises(LLMInputError, match=problem):
        validate_messages(messages, max_input_characters=10)
    assert issubclass(LLMInputError, InvalidInputError)


def test_usage_adds_up_and_keeps_unknown_token_counts_unknown() -> None:
    known = LLMUsage(requests=1, input_tokens=10, output_tokens=5, latency_ms=12.0)
    unknown = LLMUsage(requests=1, latency_ms=3.0)

    total = known + unknown + LLMUsage(requests=1, input_tokens=2, output_tokens=None)

    assert total.requests == 3
    assert total.input_tokens == 12
    assert total.output_tokens == 5
    assert total.total_tokens == 17
    assert total.latency_ms == 15.0
    assert (unknown + unknown).total_tokens is None
    assert LLMUsage().total_tokens is None


def test_generation_reports_truncation() -> None:
    complete = Generation("done", "m", FinishReason.STOP, LLMUsage())
    cut = Generation("partial", "m", FinishReason.LENGTH, LLMUsage())

    assert not complete.truncated
    assert cut.truncated


def test_options_and_limits_are_validated() -> None:
    assert GenerationOptions().max_output_tokens == 1024
    with pytest.raises(ValueError, match="positive"):
        GenerationOptions(max_output_tokens=0)
    with pytest.raises(ValueError, match="temperature"):
        GenerationOptions(temperature=2.5)
    with pytest.raises(ValueError, match="positive"):
        LLMLimits(max_input_characters=0, max_output_tokens=1)


def test_errors_are_structured_and_mapped_to_the_domain_hierarchy() -> None:
    error = LLMRateLimitedError(
        provider="p", model="m", status_code=429, attempts=3, retry_after_seconds=2.0
    )

    assert isinstance(error, LLMProviderUnavailableError)
    assert isinstance(error, DependencyUnavailableError)  # answers 503, retryable
    assert isinstance(error, LLMError)
    assert isinstance(error, DomainError)
    assert error.code == "LLM_RATE_LIMITED"
    assert (error.provider, error.model, error.status_code, error.attempts) == ("p", "m", 429, 3)
    assert error.retry_after_seconds == 2.0
    assert not isinstance(LLMRequestRejectedError(), DependencyUnavailableError)
    assert LLMResponseInvalidError(diagnostics="x").diagnostics == "x"
    assert {
        LLMError.code,
        LLMInputError.code,
        LLMProviderUnavailableError.code,
        LLMRateLimitedError.code,
        LLMRequestRejectedError.code,
        LLMResponseInvalidError.code,
    } == {
        "LLM_ERROR",
        "LLM_INPUT_INVALID",
        "LLM_PROVIDER_UNAVAILABLE",
        "LLM_RATE_LIMITED",
        "LLM_REQUEST_REJECTED",
        "LLM_RESPONSE_INVALID",
    }
