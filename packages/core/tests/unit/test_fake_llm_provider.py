"""The fake ``LLMProvider``: deterministic, scriptable, honours the port contract."""

import asyncio

import pytest

from doculens.application.llm import LLMLimits, LLMProvider
from doculens.domain.conversations import MessageRole
from doculens.domain.llm import (
    ChatMessage,
    FinishReason,
    GenerationOptions,
    LLMInputError,
    LLMProviderUnavailableError,
)
from doculens.testing.llm import FakeLLMProvider

pytestmark = pytest.mark.unit

PROMPT = [
    ChatMessage(MessageRole.SYSTEM, "Answer only from the evidence."),
    ChatMessage(MessageRole.USER, "How much did revenue grow?"),
]


async def test_the_default_answer_is_deterministic_and_echoes_the_question() -> None:
    provider: LLMProvider = FakeLLMProvider()

    first = await provider.generate(PROMPT)
    second = await provider.generate(PROMPT, options=GenerationOptions(temperature=1.0))

    assert first.text == "Fake answer to: How much did revenue grow?"
    assert first == second
    assert first.model == "fake-llm-v1"
    assert first.finish_reason is FinishReason.STOP
    assert not first.truncated
    assert first.usage.requests == 1
    assert first.usage.input_tokens == 14  # ceil(56 prompt characters / 4)
    assert first.usage.output_tokens == 11
    assert first.usage.latency_ms > 0


async def test_scripted_answers_failures_and_recorded_calls() -> None:
    provider = FakeLLMProvider(
        responses=["first", "second"], failures=[LLMProviderUnavailableError(provider="fake")]
    )

    with pytest.raises(LLMProviderUnavailableError):
        await provider.generate(PROMPT)
    assert (await provider.generate(PROMPT)).text == "first"
    assert (
        await provider.generate(PROMPT, options=GenerationOptions(max_output_tokens=5))
    ).text == "second"
    assert (await provider.generate(PROMPT)).text.startswith("Fake answer")

    assert len(provider.calls) == 4
    assert provider.calls[0] == PROMPT
    assert [o.max_output_tokens for o in provider.options_seen] == [1024, 1024, 5, 1024]


async def test_the_output_budget_truncates_like_a_real_model() -> None:
    provider = FakeLLMProvider(responses=["x" * 100])

    generation = await provider.generate(PROMPT, options=GenerationOptions(max_output_tokens=5))

    assert generation.text == "x" * 20
    assert generation.finish_reason is FinishReason.LENGTH
    assert generation.truncated
    assert generation.usage.output_tokens == 5

    capped = FakeLLMProvider(
        responses=["y" * 100], limits=LLMLimits(max_input_characters=1000, max_output_tokens=2)
    )
    assert (await capped.generate(PROMPT)).text == "y" * 8  # the configured limit wins


async def test_invalid_prompts_are_refused_before_recording_a_call() -> None:
    provider = FakeLLMProvider(limits=LLMLimits(max_input_characters=10, max_output_tokens=10))

    with pytest.raises(LLMInputError):
        await provider.generate(PROMPT)
    with pytest.raises(LLMInputError):
        await provider.generate([])

    assert provider.calls == []


async def test_a_delay_can_be_scripted_for_timeout_tests() -> None:
    provider = FakeLLMProvider(delay_seconds=5.0)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await provider.generate(PROMPT)
