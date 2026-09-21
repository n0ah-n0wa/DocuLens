# ADR-005 — LLM provider

**Status:** Accepted, provisional on OQ-17 (2026-09-22) · **Refs:** §3, §21, §22, §38, §44,
§53, §67, §73, §87.

## Context

Answer generation must sit behind an interface the application owns (§73), with the model
configurable (§21), every call accounted for (§38), failures surfacing as structured errors
that never substitute for an answer (§67), and local development possible without a cloud
account (§87). The vendor is undecided (OQ-17); this ADR fixes the port, the first adapter and
the fake, exactly as ADR-004 did for embeddings, so the answering use case can be built and
tested now and the vendor swapped later without touching it.

## Decision

| Concern       | Decision                                                                                                                                                                                                                                                                                                                                                   |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Port          | `doculens.application.llm.LLMProvider`: async `generate(messages, options)` over ordered `ChatMessage` turns (roles are the §7.7 `MessageRole`: system first, user last), plus `name` and `model`; the result is a `Generation` (text, the model that answered, `FinishReason`, `LLMUsage`)                                                                |
| Domain types  | `doculens.domain.llm`: `ChatMessage`, `GenerationOptions` (output budget, temperature), `Generation`, `FinishReason` (`stop`, `length`, `content_filter`, `other`), `LLMUsage` (requests, input and output tokens or `None`, latency) and `validate_messages`; no vendor field anywhere                                                                    |
| Errors        | Structured, vendor-free: `LLMInputError` (caller), `LLMProviderUnavailableError` / `LLMRateLimitedError` (transient, after retries; `DependencyUnavailableError` so the request answers 503 and can be retried), `LLMRequestRejectedError` (permanent), `LLMResponseInvalidError`; each carries provider, model, status, attempts and key-free diagnostics |
| Timeouts      | `LLM_TIMEOUT_SECONDS` per request (connect and read)                                                                                                                                                                                                                                                                                                       |
| Retries       | Bounded exponential backoff with full jitter (`LLM_MAX_ATTEMPTS`, `LLM_BACKOFF_BASE_SECONDS`, `LLM_BACKOFF_MAX_SECONDS`) on timeouts, connection errors, HTTP 408/409/425/429 and 5xx; `Retry-After` honoured up to the cap; other 4xx never retried. The loop lives in `doculens.infrastructure.providers` and is shared with the embedding adapter       |
| Limits        | `LLM_MAX_INPUT_CHARACTERS` refuses over-long prompts before any request; `LLM_MAX_OUTPUT_TOKENS` caps every call's output budget; `LLM_TEMPERATURE` is the default sampling temperature (0, for reproducible answers where the provider allows)                                                                                                            |
| Usage         | `LLMUsage` on every generation, taken from the provider's `usage` object when reported and never estimated; a `length` finish reason marks a truncated answer so the use case can say so instead of presenting it as complete (§21)                                                                                                                        |
| First adapter | `OpenAICompatibleLLMProvider` over `POST {LLM_API_BASE_URL}/chat/completions` with `httpx`: covers OpenAI, Azure OpenAI and self-hosted servers (Ollama, vLLM, LM Studio) with one implementation, so local development can run a real model without a cloud account                                                                                       |
| Fake          | `FakeLLMProvider` (`LLM_PROVIDER=fake`): scripted or echo answers, recorded calls and options, scriptable delays and failures, output-budget truncation; the default locally and in CI, refused in staging and production                                                                                                                                  |
| Selection     | `LLM_PROVIDER` chooses the adapter at composition time; deployed environments require a non-fake provider and an https base URL; the API key is a `SecretStr` and never logged                                                                                                                                                                             |
| Not yet       | Streaming (§44) is added to the port with the chat endpoint; prompt construction (§21, §22), citations (§23) and the usage ledger (§38) are the answering phase                                                                                                                                                                                            |

## Alternatives considered

- **Anthropic or Amazon Bedrock first.** Either may well be the production vendor (OQ-17), but
  neither offers a cloud-free local path; both are natural second adapters behind the same
  port (Bedrock reusing the `boto3` client already present).
- **A vendor SDK.** Adds a dependency and its own retry semantics; the chat-completions
  protocol is small enough that a direct `httpx` client keeps retries, timeouts, logging and
  error mapping under the repository's control and identical to the embedding adapter.
- **LangChain chat models.** Would put a framework type on the port; §72 and OQ-25 keep
  LangChain, if used at all, inside `infrastructure`, and nothing here needs it.
- **Estimating tokens when the provider omits usage.** Reports numbers that are not the
  provider's; usage stays `None` and the cost ledger treats it as unknown.

## Consequences

- No new runtime dependency (`httpx` is already present); the retry helpers of the embedding
  adapter moved to `doculens.infrastructure.providers` and are shared.
- New settings `LLM_*`; `.env.example` documents them.
- The answering use case (§21), prompt builder (§22) and citation builder (§23) build on
  `LLMProvider` and `RetrievalResult` next; the evaluation harness (§62, §63) can run against
  the fake for structure and against a real adapter for quality.
