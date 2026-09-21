"""``EmbeddingProvider`` over the OpenAI-compatible ``POST /embeddings`` API (§15, §38, §67).

Works with OpenAI, Azure OpenAI and self-hosted servers that speak the same protocol (Ollama,
vLLM, LM Studio), which keeps local development cloud-free (§87).

Behaviour:

- inputs are validated, then sent in batches bounded by item count and characters, at most
  ``max_concurrency`` batches in flight, results reassembled in input order;
- each request has a connect/read timeout; timeouts, connection errors, HTTP 408/409/425/429
  and 5xx are retried with bounded exponential backoff and full jitter, honouring ``Retry-After``
  when the provider sends it (capped by the maximum delay); other 4xx answers are not retried;
- exhausted retries surface as ``EmbeddingProviderUnavailableError`` (or the rate-limited
  subtype), rejections as ``EmbeddingRequestRejectedError``, malformed answers as
  ``EmbeddingResponseInvalidError``; every error carries provider, model, status and attempts;
- usage is taken from the response (``usage.prompt_tokens`` / ``total_tokens``) when present and
  latency is measured per request; the API key never appears in logs or errors.
"""

import asyncio
import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from doculens.application.embeddings import EmbeddingLimits, batched, validate_inputs
from doculens.domain.embeddings import (
    EmbeddingProviderUnavailableError,
    EmbeddingRateLimitedError,
    EmbeddingRequestRejectedError,
    EmbeddingResponseInvalidError,
    EmbeddingResult,
    EmbeddingUsage,
    Vector,
)
from doculens.infrastructure.providers import (
    RETRYABLE_STATUS_CODES,
    Jitter,
    RetryPolicy,
    Sleeper,
    diagnostics_from,
    retry_after_seconds,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = "openai-compatible"


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str | None
    model: str
    dimensions: int | None
    timeout_seconds: float
    limits: EmbeddingLimits
    retry: RetryPolicy
    max_concurrency: int = 4


class OpenAICompatibleEmbeddingProvider:
    name = PROVIDER_NAME

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleeper = asyncio.sleep,
        jitter: Jitter = random.random,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._jitter = jitter
        self._slots = asyncio.Semaphore(config.max_concurrency)
        headers = {"Accept": "application/json", "User-Agent": "doculens"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(config.timeout_seconds),
            transport=transport,
        )

    @property
    def model(self) -> str:
        return self._config.model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        validate_inputs(texts, self._config.limits)
        if not texts:
            return EmbeddingResult(vectors=(), model=self.model, dimensions=0)
        batches = list(batched(texts, self._config.limits))
        # A task group cancels the sibling batches when one fails permanently, so a rejected
        # request does not leave paid requests running for a result nobody will use.
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(self._embed_batch(batch)) for _, batch in batches]
        except ExceptionGroup as failures:
            # Report the first failure as itself; the group only served to cancel the rest.
            raise failures.exceptions[0] from failures
        vectors: list[Vector] = []
        usage = EmbeddingUsage()
        for task in tasks:
            result = task.result()
            vectors.extend(result.vectors)
            usage += result.usage
        return EmbeddingResult.build(vectors, model=self.model, usage=usage)

    async def embed_query(self, text: str) -> EmbeddingResult:
        validate_inputs([text], self._config.limits)
        return await self._embed_batch([text])

    # -- one batch with retries --------------------------------------------------------------------

    async def _embed_batch(self, batch: list[str]) -> EmbeddingResult:
        async with self._slots:
            attempt = 0
            while True:
                attempt += 1
                try:
                    return await self._request(batch, attempt)
                except _RetryableError as failure:
                    if attempt >= self._config.retry.max_attempts:
                        raise failure.exhausted(attempt) from failure.cause
                    delay = self._config.retry.delay(
                        attempt, jitter=self._jitter(), retry_after=failure.retry_after
                    )
                    logger.warning(
                        "embedding request will be retried",
                        extra={
                            "operation": "embedding.retry",
                            "provider": self.name,
                            "model": self.model,
                            "attempt": attempt,
                            "status": failure.status_code,
                            "delay_seconds": round(delay, 3),
                            "reason": failure.reason,
                        },
                    )
                    await self._sleep(delay)

    async def _request(self, batch: list[str], attempt: int) -> EmbeddingResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": batch,
            "encoding_format": "float",
        }
        if self._config.dimensions is not None:
            payload["dimensions"] = self._config.dimensions
        started = time.perf_counter()
        try:
            response = await self._client.post("/embeddings", json=payload)
        except httpx.TimeoutException as exc:
            raise _RetryableError(reason="timeout", cause=exc, provider=self) from exc
        except httpx.TransportError as exc:
            raise _RetryableError(reason="connection", cause=exc, provider=self) from exc
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise _RetryableError(
                reason=f"http {response.status_code}",
                status_code=response.status_code,
                retry_after=retry_after_seconds(response.headers),
                provider=self,
            )
        if response.status_code >= 400:  # noqa: PLR2004 - HTTP client/server error boundary
            raise EmbeddingRequestRejectedError(
                provider=self.name,
                model=self.model,
                status_code=response.status_code,
                attempts=attempt,
                diagnostics=diagnostics_from(response),
            )
        result = self._parse(response, expected=len(batch), latency_ms=latency_ms)
        logger.info(
            "embedded batch",
            extra={
                "operation": "embedding.batch",
                "provider": self.name,
                "model": self.model,
                "inputs": len(batch),
                "tokens": result.usage.tokens,
                "latency_ms": round(latency_ms, 1),
                "attempt": attempt,
            },
        )
        return result

    def _parse(
        self, response: httpx.Response, *, expected: int, latency_ms: float
    ) -> EmbeddingResult:
        try:
            body = response.json()
        except ValueError as exc:
            raise self._invalid(diagnostics="response is not JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise self._invalid(diagnostics="response has no data list")
        items = body["data"]
        if len(items) != expected:
            raise self._invalid(diagnostics=f"expected {expected} embeddings, got {len(items)}")
        ordered: list[Sequence[float] | None] = [None] * expected
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise self._invalid(diagnostics="an item has no embedding list")
            index = item.get("index")
            if (
                not isinstance(index, int)
                or not 0 <= index < expected
                or ordered[index] is not None
            ):
                raise self._invalid(
                    diagnostics="embedding indexes are not a permutation of the inputs"
                )
            ordered[index] = item["embedding"]
        vectors = [vector for vector in ordered if vector is not None]
        raw_usage = body.get("usage")
        usage_body: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        tokens = usage_body.get("prompt_tokens", usage_body.get("total_tokens"))
        usage = EmbeddingUsage(
            requests=1,
            tokens=int(tokens) if isinstance(tokens, int) and tokens >= 0 else None,
            latency_ms=latency_ms,
        )
        reported_model = body.get("model")
        model = reported_model if isinstance(reported_model, str) and reported_model else self.model
        try:
            result = EmbeddingResult.build(vectors, model=model, usage=usage)
        except EmbeddingResponseInvalidError as error:
            raise self._invalid(diagnostics=error.diagnostics or "invalid vectors") from error
        wanted = self._config.dimensions
        if wanted is not None and result.dimensions != wanted:
            raise self._invalid(
                diagnostics=f"expected {wanted} dimensions, got {result.dimensions}"
            )
        return result

    def _invalid(self, *, diagnostics: str) -> EmbeddingResponseInvalidError:
        return EmbeddingResponseInvalidError(
            provider=self.name, model=self.model, diagnostics=diagnostics
        )


class _RetryableError(Exception):
    """Internal: a failure the retry loop may try again."""

    def __init__(
        self,
        *,
        reason: str,
        provider: "OpenAICompatibleEmbeddingProvider",
        cause: Exception | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cause = cause
        self.status_code = status_code
        self.retry_after = retry_after
        self._provider = provider

    def exhausted(self, attempts: int) -> EmbeddingProviderUnavailableError:
        error_type = (
            EmbeddingRateLimitedError
            if self.status_code == 429  # noqa: PLR2004 - HTTP Too Many Requests
            else EmbeddingProviderUnavailableError
        )
        return error_type(
            provider=self._provider.name,
            model=self._provider.model,
            status_code=self.status_code,
            attempts=attempts,
            retry_after_seconds=self.retry_after,
            diagnostics=f"{self.reason} after {attempts} attempts",
        )
