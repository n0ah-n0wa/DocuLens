# ADR-004 — Embedding provider

**Status:** Accepted, provisional on OQ-17 (2026-09-20) · **Refs:** §3, §15, §32, §38, §53, §67,
§73, §87.

## Context

Embedding generation must sit behind an interface with `embed_documents` and `embed_query`, the
application must not couple to a vendor, model configuration must be external (§15, §73), every
call must be accounted for (§38), an unavailable provider must leave processing retryable (§67),
and local development must run without production infrastructure (§87). The vendor itself is
undecided (OQ-17); this ADR fixes the port and the first adapter, not the final vendor.

## Decision

| Concern       | Decision                                                                                                                                                                                                                                                                          |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Port          | `doculens.application.embeddings.EmbeddingProvider`: async `embed_documents(texts)` and `embed_query(text)`, plus `name` and `model`; results are `EmbeddingResult` (vectors in input order, model, dimensions, usage)                                                            |
| Errors        | Structured, vendor-free: `EmbeddingInputError` (caller), `EmbeddingProviderUnavailableError` / `EmbeddingRateLimitedError` (transient, after retries; a `DependencyUnavailableError` so jobs retry), `EmbeddingRequestRejectedError` (permanent), `EmbeddingResponseInvalidError` |
| Usage         | `EmbeddingUsage(requests, tokens, latency_ms)` on every result, summed across batches; tokens are `None` when the provider does not report them, never estimated silently (§38)                                                                                                   |
| Batching      | `EMBEDDING_MAX_BATCH_SIZE` items and `EMBEDDING_MAX_BATCH_CHARACTERS` per request, `EMBEDDING_MAX_INPUT_CHARACTERS` per input, `EMBEDDING_MAX_CONCURRENCY` requests in flight; order preserved; empty and over-long inputs refused before any request                             |
| Retries       | Bounded exponential backoff with full jitter (`EMBEDDING_MAX_ATTEMPTS`, `EMBEDDING_BACKOFF_BASE_SECONDS`, `EMBEDDING_BACKOFF_MAX_SECONDS`) on timeouts, connection errors, HTTP 408/409/425/429 and 5xx; `Retry-After` honoured up to the cap; other 4xx never retried            |
| Timeouts      | `EMBEDDING_TIMEOUT_SECONDS` per request (connect and read)                                                                                                                                                                                                                        |
| First adapter | `OpenAICompatibleEmbeddingProvider` over `POST {EMBEDDING_API_BASE_URL}/embeddings` with `httpx`: covers OpenAI, Azure OpenAI and self-hosted servers (Ollama, vLLM) with one implementation, so local development can run a real model without a cloud account                   |
| Fake          | `FakeEmbeddingProvider` (`EMBEDDING_PROVIDER=fake`): deterministic hashed unit vectors, recorded calls, scriptable failures; the default locally and in CI, refused in staging and production                                                                                     |
| Selection     | `EMBEDDING_PROVIDER` chooses the adapter at composition time; deployed environments require a non-fake provider and an https base URL; the API key is a `SecretStr` and never logged                                                                                              |
| Staleness     | The provider name and model travel with vectors and chunk metadata so re-indexing can detect a changed model (§32)                                                                                                                                                                |

## Alternatives considered

- **Amazon Bedrock first.** Stays inside the AWS IAM story with no extra secret, but offers no
  local path; it remains the natural second adapter behind the same port once OQ-17 is decided,
  using the `boto3` client already present.
- **Vendor SDK (`openai` package).** Adds a dependency and its own retry semantics; the protocol
  is small enough that a direct `httpx` client keeps retries, timeouts and logging under the
  repository's control and identical across compatible servers.
- **Estimating tokens locally when the provider omits usage.** Would report numbers that are
  not the provider's; usage stays `None` and the cost ledger treats it as unknown.

## Consequences

- New runtime dependency `httpx` in `doculens-core`.
- ChromaDB integration and the `EMBEDDING` pipeline stage are the next phase; nothing calls the
  provider from the processor yet.
- Cost estimation (§38) needs per-model pricing, which arrives with the usage ledger.
