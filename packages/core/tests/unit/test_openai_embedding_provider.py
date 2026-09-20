"""The OpenAI-compatible adapter against a scripted HTTP transport: no network, no vendor SDK."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import httpx
import pytest

from doculens.application.embeddings import EmbeddingLimits
from doculens.domain.embeddings import (
    EmbeddingInputError,
    EmbeddingProviderUnavailableError,
    EmbeddingRateLimitedError,
    EmbeddingRequestRejectedError,
    EmbeddingResponseInvalidError,
)
from doculens.infrastructure.embeddings import (
    OpenAICompatibleConfig,
    OpenAICompatibleEmbeddingProvider,
    RetryPolicy,
)

pytestmark = pytest.mark.unit

API_KEY = "sk-test-key-that-must-never-be-logged"  # gitleaks:allow
LIMITS = EmbeddingLimits(max_batch_size=2, max_batch_characters=1000, max_input_characters=100)
RETRY = RetryPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=4.0)


def embeddings_response(
    inputs: list[str], *, dimensions: int = 3, tokens: int | None = 7
) -> dict[str, object]:
    data = [
        {"object": "embedding", "index": index, "embedding": [float(index)] * dimensions}
        for index, _ in enumerate(inputs)
    ]
    body: dict[str, object] = {"object": "list", "data": data, "model": "text-embedding-test"}
    if tokens is not None:
        body["usage"] = {"prompt_tokens": tokens, "total_tokens": tokens}
    return body


@dataclass
class Script:
    """Answers requests in order; each step is a status/body pair or an exception to raise."""

    steps: list[object]
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0) if self.steps else None
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(request)  # type: ignore[no-any-return]
        if step is None:
            inputs = json.loads(request.content)["input"]
            return httpx.Response(200, json=embeddings_response(inputs))
        status, body = cast("tuple[int, dict[str, object]]", step)
        return httpx.Response(status, json=body, headers={"content-type": "application/json"})


@dataclass
class Clock:
    sleeps: list[float] = field(default_factory=list)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def provider(
    script: Script,
    clock: Clock,
    *,
    retry: RetryPolicy = RETRY,
    dimensions: int | None = None,
    jitter: Callable[[], float] = lambda: 1.0,
    max_concurrency: int = 4,
) -> OpenAICompatibleEmbeddingProvider:
    return OpenAICompatibleEmbeddingProvider(
        OpenAICompatibleConfig(
            base_url="https://embeddings.example.test/v1/",
            api_key=API_KEY,
            model="text-embedding-test",
            dimensions=dimensions,
            timeout_seconds=5.0,
            limits=LIMITS,
            retry=retry,
            max_concurrency=max_concurrency,
        ),
        transport=httpx.MockTransport(script.handler),
        sleep=clock.sleep,
        jitter=jitter,
    )


async def test_documents_are_batched_and_reassembled_in_order_with_usage() -> None:
    script, clock = Script([None, None, None]), Clock()
    subject = provider(script, clock)

    result = await subject.embed_documents(["a", "b", "c", "d", "e"])

    assert [json.loads(r.content)["input"] for r in script.requests] == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]
    assert len(result.vectors) == 5
    assert result.vectors[0] == (0.0, 0.0, 0.0)
    assert result.vectors[1] == (1.0, 1.0, 1.0)
    assert result.vectors[4] == (0.0, 0.0, 0.0)
    assert result.dimensions == 3
    assert result.usage.requests == 3
    assert result.usage.tokens == 21
    assert result.usage.latency_ms >= 0
    request = script.requests[0]
    assert request.url == httpx.URL("https://embeddings.example.test/v1/embeddings")
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(request.content)
    assert body["model"] == "text-embedding-test"
    assert body["encoding_format"] == "float"
    assert "dimensions" not in body


async def test_query_embedding_and_requested_dimensions() -> None:
    script, clock = Script([None]), Clock()
    subject = provider(script, clock, dimensions=3)

    result = await subject.embed_query("what changed?")

    assert result.single == (0.0, 0.0, 0.0)
    assert json.loads(script.requests[0].content)["dimensions"] == 3
    assert json.loads(script.requests[0].content)["input"] == ["what changed?"]


async def test_an_empty_input_list_makes_no_request() -> None:
    script, clock = Script([]), Clock()

    result = await provider(script, clock).embed_documents([])

    assert result.vectors == ()
    assert script.requests == []


async def test_invalid_inputs_never_reach_the_network() -> None:
    script, clock = Script([]), Clock()
    subject = provider(script, clock)

    with pytest.raises(EmbeddingInputError):
        await subject.embed_documents(["fine", ""])
    with pytest.raises(EmbeddingInputError):
        await subject.embed_query("x" * 101)
    assert script.requests == []


async def test_transient_failures_are_retried_with_bounded_exponential_backoff() -> None:
    script = Script([(503, {"error": {"message": "overloaded"}}), httpx.ConnectError("boom"), None])
    clock = Clock()

    result = await provider(script, clock, jitter=lambda: 0.5).embed_query("q")

    assert result.single == (0.0, 0.0, 0.0)
    assert len(script.requests) == 3
    assert clock.sleeps == [0.25, 0.5]  # 0.5 * 2**0 * jitter, 0.5 * 2**1 * jitter


async def test_backoff_is_capped_and_retry_after_is_honoured_up_to_the_cap() -> None:
    script = Script(
        [
            (429, {"error": {"message": "slow down"}}),
            lambda _: httpx.Response(429, json={}, headers={"retry-after": "2"}),
            lambda _: httpx.Response(429, json={}, headers={"retry-after": "3600"}),
            None,
        ]
    )
    clock = Clock()
    policy = RetryPolicy(max_attempts=4, base_delay_seconds=3.0, max_delay_seconds=4.0)

    await provider(script, clock, retry=policy, jitter=lambda: 1.0).embed_query("q")

    assert clock.sleeps == [3.0, 2.0, 4.0]


async def test_exhausted_retries_become_a_structured_unavailable_error() -> None:
    script = Script([(500, {}), (502, {}), (503, {})])
    clock = Clock()

    with pytest.raises(EmbeddingProviderUnavailableError) as excinfo:
        await provider(script, clock).embed_query("q")

    error = excinfo.value
    assert error.code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert (error.provider, error.model, error.status_code, error.attempts) == (
        "openai-compatible",
        "text-embedding-test",
        503,
        3,
    )
    assert len(script.requests) == 3
    assert len(clock.sleeps) == 2


async def test_persistent_rate_limiting_is_reported_as_such() -> None:
    script = Script([(429, {})] * 3)

    with pytest.raises(EmbeddingRateLimitedError) as excinfo:
        await provider(script, Clock()).embed_query("q")

    assert excinfo.value.status_code == 429
    assert excinfo.value.attempts == 3


async def test_timeouts_are_retried_then_reported() -> None:
    script = Script([httpx.ReadTimeout("slow")] * 3)

    with pytest.raises(EmbeddingProviderUnavailableError) as excinfo:
        await provider(script, Clock()).embed_query("q")

    assert "timeout" in (excinfo.value.diagnostics or "")
    assert excinfo.value.status_code is None


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
async def test_client_errors_are_not_retried_and_carry_the_status(status: int) -> None:
    script = Script([(status, {"error": {"message": "nope: " + "x" * 1000}})])
    clock = Clock()

    with pytest.raises(EmbeddingRequestRejectedError) as excinfo:
        await provider(script, clock).embed_query("q")

    assert excinfo.value.status_code == status
    assert excinfo.value.attempts == 1
    assert len(script.requests) == 1
    assert clock.sleeps == []
    assert len(excinfo.value.diagnostics or "") < 400  # bounded
    assert API_KEY not in str(excinfo.value.__dict__)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"data": "not a list"}, "no data list"),
        ({"data": [{"index": 0, "embedding": [1.0]}]}, "expected 2 embeddings"),
        (
            {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}]},
            "permutation",
        ),
        (
            {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}]},
            "inconsistent",
        ),
        (
            {"data": [{"index": 0, "embedding": "x"}, {"index": 1, "embedding": [1.0]}]},
            "no embedding list",
        ),
    ],
)
async def test_malformed_responses_are_reported_not_retried(
    body: dict[str, object], reason: str
) -> None:
    script = Script([(200, body)])

    with pytest.raises(EmbeddingResponseInvalidError) as excinfo:
        await provider(script, Clock()).embed_documents(["a", "b"])

    assert reason in (excinfo.value.diagnostics or "")
    assert len(script.requests) == 1


async def test_a_non_json_body_is_invalid() -> None:
    script = Script([lambda _: httpx.Response(200, content=b"<html>oops</html>")])

    with pytest.raises(EmbeddingResponseInvalidError):
        await provider(script, Clock()).embed_query("q")


async def test_unexpected_dimensions_are_refused() -> None:
    script = Script([None])

    with pytest.raises(EmbeddingResponseInvalidError) as excinfo:
        await provider(script, Clock(), dimensions=8).embed_query("q")

    assert "expected 8 dimensions" in (excinfo.value.diagnostics or "")


async def test_usage_is_optional_and_the_reported_model_is_recorded() -> None:
    script = Script([(200, {**embeddings_response(["a"], tokens=None), "model": "served-model"})])

    result = await provider(script, Clock()).embed_query("a")

    assert result.usage.tokens is None
    assert result.usage.requests == 1
    assert result.model == "served-model"


async def test_batches_run_with_bounded_concurrency() -> None:
    in_flight = 0
    peak = 0

    def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        in_flight -= 1
        inputs = json.loads(request.content)["input"]
        return httpx.Response(200, json=embeddings_response(inputs))

    script = Script([slow] * 6)

    await provider(script, Clock(), max_concurrency=2).embed_documents([str(i) for i in range(12)])

    assert len(script.requests) == 6
    assert peak <= 2


async def test_logs_carry_metadata_but_never_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="doculens.infrastructure.embeddings.openai_compatible")
    script = Script([(503, {}), None])

    await provider(script, Clock()).embed_query("q")

    rendered = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "embedding.retry" in rendered
    assert "embedding.batch" in rendered
    assert API_KEY not in rendered
