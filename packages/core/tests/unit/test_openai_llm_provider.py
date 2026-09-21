"""The OpenAI-compatible LLM adapter against a scripted HTTP transport: no network, no SDK."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

from doculens.application.llm import LLMLimits
from doculens.domain.conversations import MessageRole
from doculens.domain.llm import (
    ChatMessage,
    FinishReason,
    GenerationOptions,
    LLMInputError,
    LLMProviderUnavailableError,
    LLMRateLimitedError,
    LLMRequestRejectedError,
    LLMResponseInvalidError,
)
from doculens.infrastructure.llm import OpenAICompatibleLLMConfig, OpenAICompatibleLLMProvider
from doculens.infrastructure.providers import RetryPolicy

pytestmark = pytest.mark.unit

API_KEY = "sk-test-key-that-must-never-be-logged"  # gitleaks:allow
LIMITS = LLMLimits(max_input_characters=500, max_output_tokens=256)
RETRY = RetryPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=4.0)
PROMPT = [
    ChatMessage(MessageRole.SYSTEM, "Answer only from the evidence."),
    ChatMessage(MessageRole.USER, "How much did revenue grow?"),
]


def completion(
    text: str | None = "Revenue grew twelve percent.",
    *,
    finish_reason: str = "stop",
    usage: dict[str, object] | None = None,
    model: str = "gpt-test",
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant"}
    if text is not None:
        message["content"] = text
    body: dict[str, object] = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


@dataclass
class Script:
    """Answers requests in order; records what was sent."""

    steps: list[Callable[[httpx.Request], httpx.Response]]
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        return step(request)


def ok(body: dict[str, object]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(200, json=body)


def status(code: int, **headers: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(
        code, json={"error": {"message": f"failure {code}"}}, headers=headers
    )


def timeout(_: httpx.Request) -> httpx.Response:
    message = "slow"
    raise httpx.ReadTimeout(message)


@dataclass
class Clock:
    slept: list[float] = field(default_factory=list)

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def provider(
    script: Script, *, retry: RetryPolicy = RETRY, clock: Clock | None = None
) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        OpenAICompatibleLLMConfig(
            base_url="https://llm.example.test/v1/",
            api_key=API_KEY,
            model="gpt-configured",
            timeout_seconds=5.0,
            limits=LIMITS,
            retry=retry,
        ),
        transport=httpx.MockTransport(script.handler),
        sleep=(clock or Clock()).sleep,
        jitter=lambda: 1.0,
    )


async def test_a_generation_carries_text_model_finish_reason_and_usage() -> None:
    script = Script(
        [ok(completion(usage={"prompt_tokens": 40, "completion_tokens": 7, "total_tokens": 47}))]
    )

    generation = await provider(script).generate(
        PROMPT, options=GenerationOptions(max_output_tokens=100, temperature=0.2)
    )

    assert generation.text == "Revenue grew twelve percent."
    assert generation.model == "gpt-test"  # the model that answered, not just the configured one
    assert generation.finish_reason is FinishReason.STOP
    assert generation.usage.requests == 1
    assert generation.usage.input_tokens == 40
    assert generation.usage.output_tokens == 7
    assert generation.usage.latency_ms >= 0
    (request,) = script.requests
    assert request.url == "https://llm.example.test/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    payload = json.loads(request.content)
    assert payload["model"] == "gpt-configured"
    assert payload["messages"] == [
        {"role": "system", "content": "Answer only from the evidence."},
        {"role": "user", "content": "How much did revenue grow?"},
    ]
    assert payload["max_tokens"] == 100
    assert payload["temperature"] == 0.2
    assert payload["stream"] is False


async def test_the_output_budget_is_capped_by_the_configured_limit() -> None:
    script = Script([ok(completion())])

    await provider(script).generate(PROMPT, options=GenerationOptions(max_output_tokens=10_000))

    assert json.loads(script.requests[0].content)["max_tokens"] == LIMITS.max_output_tokens


async def test_transient_failures_are_retried_with_backoff_and_retry_after() -> None:
    clock = Clock()
    script = Script([status(503), status(429, **{"Retry-After": "2"}), ok(completion())])

    generation = await provider(script, clock=clock).generate(PROMPT)

    assert generation.text == "Revenue grew twelve percent."
    assert len(script.requests) == 3
    assert clock.slept == [0.5, 2.0]  # base * jitter, then the provider's Retry-After


async def test_exhausted_retries_surface_as_unavailable_or_rate_limited() -> None:
    unavailable = provider(Script([status(502)]), clock=Clock())
    with pytest.raises(LLMProviderUnavailableError) as failure:
        await unavailable.generate(PROMPT)
    assert failure.value.status_code == 502
    assert failure.value.attempts == 3
    assert failure.value.provider == "openai-compatible"
    assert failure.value.model == "gpt-configured"

    limited = provider(Script([status(429, **{"Retry-After": "1"})]), clock=Clock())
    with pytest.raises(LLMRateLimitedError) as limit:
        await limited.generate(PROMPT)
    assert limit.value.retry_after_seconds == 1.0

    slow = provider(Script([timeout]), clock=Clock())
    with pytest.raises(LLMProviderUnavailableError) as timed_out:
        await slow.generate(PROMPT)
    assert "timeout" in (timed_out.value.diagnostics or "")


async def test_permanent_rejections_are_not_retried_and_never_carry_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = Script([status(400)])

    with caplog.at_level(logging.DEBUG), pytest.raises(LLMRequestRejectedError) as rejected:
        await provider(script).generate(PROMPT)

    assert len(script.requests) == 1
    assert rejected.value.status_code == 400
    assert rejected.value.diagnostics == "http 400: failure 400"
    assert API_KEY not in str(rejected.value)
    assert API_KEY not in caplog.text


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ({"choices": []}, "no choices"),
        ({"choices": "nope"}, "no choices"),
        ({"choices": [{"message": {"role": "assistant"}, "finish_reason": "stop"}]}, "no text"),
        ({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}, "empty"),
        ([], "not an object"),
    ],
)
async def test_malformed_answers_are_reported_as_invalid(body: object, problem: str) -> None:
    script = Script([lambda _: httpx.Response(200, json=body)])

    with pytest.raises(LLMResponseInvalidError, match="") as invalid:
        await provider(script).generate(PROMPT)

    assert problem in (invalid.value.diagnostics or "")


async def test_non_json_answers_are_invalid() -> None:
    script = Script([lambda _: httpx.Response(200, content=b"<html>oops</html>")])

    with pytest.raises(LLMResponseInvalidError) as invalid:
        await provider(script).generate(PROMPT)

    assert invalid.value.diagnostics == "response is not JSON"


async def test_finish_reasons_and_missing_usage_are_mapped_honestly() -> None:
    cut = await provider(Script([ok(completion("partial", finish_reason="length"))])).generate(
        PROMPT
    )
    assert cut.truncated
    assert cut.usage.input_tokens is None
    assert cut.usage.output_tokens is None
    assert cut.usage.total_tokens is None

    filtered = await provider(
        Script([ok(completion(None, finish_reason="content_filter"))])
    ).generate(PROMPT)
    assert filtered.text == ""
    assert filtered.finish_reason is FinishReason.CONTENT_FILTER

    odd = await provider(Script([ok(completion(finish_reason="tool_calls", model=""))])).generate(
        PROMPT
    )
    assert odd.finish_reason is FinishReason.OTHER
    assert odd.model == "gpt-configured"  # falls back to the configured model

    bogus_usage = await provider(
        Script([ok(completion(usage={"prompt_tokens": -1, "completion_tokens": "7"}))])
    ).generate(PROMPT)
    assert bogus_usage.usage.total_tokens is None


async def test_invalid_prompts_never_reach_the_network() -> None:
    script = Script([ok(completion())])

    with pytest.raises(LLMInputError):
        await provider(script).generate([ChatMessage(MessageRole.USER, "x" * 501)])
    with pytest.raises(LLMInputError):
        await provider(script).generate([])

    assert script.requests == []
