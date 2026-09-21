"""``LLMProvider`` over the OpenAI-compatible ``POST /chat/completions`` API (§21, §38, §67).

Works with OpenAI, Azure OpenAI and self-hosted servers that speak the same protocol (Ollama,
vLLM, LM Studio), which keeps local development cloud-free (§87). Behaviour:

- the prompt is validated first and ``max_output_tokens`` is capped by the configured limit;
- each request has a connect/read timeout; timeouts, connection errors, HTTP 408/409/425/429
  and 5xx are retried with bounded exponential backoff and full jitter, honouring
  ``Retry-After`` (capped by the maximum delay); other 4xx answers are never retried;
- exhausted retries surface as ``LLMProviderUnavailableError`` (or ``LLMRateLimitedError``),
  rejections as ``LLMRequestRejectedError``, malformed answers as ``LLMResponseInvalidError``;
  every error carries provider, model, status and attempts, never the key or the prompt;
- usage comes from ``usage.prompt_tokens`` / ``usage.completion_tokens`` when present and the
  latency is measured per request; the finish reason is mapped to ``FinishReason``.
"""

import asyncio
import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from doculens.application.llm import LLMLimits
from doculens.domain.llm import (
    ChatMessage,
    FinishReason,
    Generation,
    GenerationOptions,
    LLMProviderUnavailableError,
    LLMRateLimitedError,
    LLMRequestRejectedError,
    LLMResponseInvalidError,
    LLMUsage,
    validate_messages,
)
from doculens.infrastructure.providers import (
    RETRYABLE_STATUS_CODES,
    Jitter,
    RetryableError,
    RetryPolicy,
    Sleeper,
    diagnostics_from,
    retry_after_seconds,
    run_with_retries,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = "openai-compatible"
_FINISH_REASONS = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "content_filter": FinishReason.CONTENT_FILTER,
}


@dataclass(frozen=True, slots=True)
class OpenAICompatibleLLMConfig:
    base_url: str
    api_key: str | None
    model: str
    timeout_seconds: float
    limits: LLMLimits
    retry: RetryPolicy


class OpenAICompatibleLLMProvider:
    name = PROVIDER_NAME

    def __init__(
        self,
        config: OpenAICompatibleLLMConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleeper = asyncio.sleep,
        jitter: Jitter = random.random,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._jitter = jitter
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

    async def generate(
        self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None
    ) -> Generation:
        validate_messages(messages, max_input_characters=self._config.limits.max_input_characters)
        chosen = options or GenerationOptions()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": message.role.value.lower(), "content": message.content}
                for message in messages
            ],
            "max_tokens": min(chosen.max_output_tokens, self._config.limits.max_output_tokens),
            "temperature": chosen.temperature,
            "stream": False,
        }
        return await run_with_retries(
            lambda attempt: self._request(payload, attempt),
            policy=self._config.retry,
            sleep=self._sleep,
            jitter=self._jitter,
            exhausted=self._exhausted,
            logger=logger,
            operation="llm.retry",
            extra={"provider": self.name, "model": self.model},
        )

    async def _request(self, payload: dict[str, Any], attempt: int) -> Generation:
        started = time.perf_counter()
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise RetryableError(reason="timeout", cause=exc) from exc
        except httpx.TransportError as exc:
            raise RetryableError(reason="connection", cause=exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise RetryableError(
                reason=f"http {response.status_code}",
                status_code=response.status_code,
                retry_after=retry_after_seconds(response.headers),
            )
        if response.status_code >= 400:  # noqa: PLR2004 - HTTP client/server error boundary
            raise LLMRequestRejectedError(
                provider=self.name,
                model=self.model,
                status_code=response.status_code,
                attempts=attempt,
                diagnostics=diagnostics_from(response),
            )
        generation = self._parse(response, latency_ms=latency_ms)
        logger.info(
            "generated answer",
            extra={
                "operation": "llm.generate",
                "provider": self.name,
                "model": generation.model,
                "finish_reason": generation.finish_reason.value,
                "input_tokens": generation.usage.input_tokens,
                "output_tokens": generation.usage.output_tokens,
                "latency_ms": round(latency_ms, 1),
                "attempt": attempt,
            },
        )
        return generation

    def _parse(self, response: httpx.Response, *, latency_ms: float) -> Generation:
        try:
            body = response.json()
        except ValueError as exc:
            raise self._invalid(diagnostics="response is not JSON") from exc
        if not isinstance(body, dict):
            raise self._invalid(diagnostics="response is not an object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise self._invalid(diagnostics="response has no choices")
        choice = choices[0]
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        raw_reason = choice.get("finish_reason")
        finish_reason = (
            _FINISH_REASONS.get(raw_reason, FinishReason.OTHER)
            if isinstance(raw_reason, str)
            else FinishReason.OTHER
        )
        if not isinstance(content, str):
            if finish_reason is FinishReason.CONTENT_FILTER:
                content = ""
            else:
                raise self._invalid(diagnostics="the choice has no text content")
        if not content and finish_reason is not FinishReason.CONTENT_FILTER:
            raise self._invalid(diagnostics="the answer is empty")
        raw_usage = body.get("usage")
        usage_body: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        reported_model = body.get("model")
        return Generation(
            text=content,
            model=reported_model
            if isinstance(reported_model, str) and reported_model
            else self.model,
            finish_reason=finish_reason,
            usage=LLMUsage(
                requests=1,
                input_tokens=_token_count(usage_body.get("prompt_tokens")),
                output_tokens=_token_count(usage_body.get("completion_tokens")),
                latency_ms=latency_ms,
            ),
        )

    def _invalid(self, *, diagnostics: str) -> LLMResponseInvalidError:
        return LLMResponseInvalidError(
            provider=self.name, model=self.model, diagnostics=diagnostics
        )

    def _exhausted(self, failure: RetryableError, attempts: int) -> Exception:
        error_type = LLMRateLimitedError if failure.rate_limited else LLMProviderUnavailableError
        return error_type(
            provider=self.name,
            model=self.model,
            status_code=failure.status_code,
            attempts=attempts,
            retry_after_seconds=failure.retry_after,
            diagnostics=f"{failure.reason} after {attempts} attempts",
        )


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


__all__ = ["PROVIDER_NAME", "OpenAICompatibleLLMConfig", "OpenAICompatibleLLMProvider"]
