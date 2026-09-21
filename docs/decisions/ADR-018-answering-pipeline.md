# ADR-018 — Answering pipeline and citations

**Status:** Accepted (2026-09-22) · **Resolves provisionally:** OQ-29 (insufficient evidence),
OQ-30 (citation attribution) · **Touches:** OQ-8 (conversation scope), OQ-18 (failed answers) ·
**Refs:** §7.6 to §7.8, §17, §21, §23, §25, §26, §28, §38, §67.

## Context

The retrieval pipeline, the grounded prompt and the language-model port exist; §21 requires the
answer to be grounded or to say that evidence is insufficient, §23 requires citations that
reference the chunks actually used, §26 requires follow-up questions to be rewritten for
retrieval while the stored message stays as asked, and §28 requires conversations and messages
to be persisted. The specification leaves open who decides "insufficient" (OQ-29), how citations
are attributed (OQ-30), how a conversation scopes retrieval (OQ-8) and what is persisted when
generation fails (OQ-18).

## Decision

| Concern               | Decision                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Use case              | `doculens.application.answering.AnswerService.answer(owner, question, conversation_id, document_ids, collection_id)` runs question → rewriting → retrieval (reranking and context assembly inside) → prompt → generation → reference validation → citations → persistence; `RagService.answer` is the boundary                                                                                   |
| Result                | `AnswerResult`: `outcome` (`answered` or `insufficient_evidence`), `answer`, `citations`, the conversation and message ids, `RetrievalMetadata` (question, retrieval query, whether rewritten, retriever, scope, hits, evidence, context size, reranking report, prompt version, model, finish reason, invalid references), `AnswerUsage` (embeddings, generation, rewriting) and `AnswerTiming` |
| Insufficient (OQ-29)  | Decided both ways, as proposed: an empty context short-circuits before any model call and answers with the exact policy sentence; a model answer that starts with that sentence is reported as `insufficient_evidence` and carries no citations. The sentence is `INSUFFICIENT_EVIDENCE_STATEMENT` of the prompt module                                                                          |
| Citations (OQ-30)     | Numbered references: the model cites `[n]`; `n` is a citation only if it names a context item of this prompt. Valid references become `Citation` rows (document, page, chunk, quoted evidence text bounded by `CITATION_MAX_QUOTE_CHARACTERS`, retrieval score, reranking score when reranked, order of first mention); invalid ones are removed from the answer and counted                     |
| Blocked answers (§22) | A model answer that reproduces the system policy (two or more of its section lines: a document or the question asked the model to reveal it) is withheld: outcome `blocked`, a fixed statement, no citations, the violation named in the result and logged; the leaked text is never persisted or returned                                                                                       |
| Latency budgets       | Retrieval is bounded by `RETRIEVAL_TIMEOUT_SECONDS`, rewriting by `QUERY_REWRITE_TIMEOUT_SECONDS` (fail-open) and the answer's model call by `GENERATION_TIMEOUT_SECONDS` (retries included; expiry raises the retryable `GENERATION_TIMEOUT` and persists nothing), so one question never outlives a predictable budget                                                                         |
| Prompt budget         | Settings validation requires the system policy plus `CONTEXT_MAX_CHARACTERS`, `PROMPT_MAX_HISTORY_CHARACTERS` and `RETRIEVAL_MAX_QUERY_CHARACTERS` to fit `LLM_MAX_INPUT_CHARACTERS`, so a well-formed prompt is never refused by the provider's input limit; the rewriter additionally fails open when a provider refuses a prompt                                                              |
| Groundedness signals  | `AnswerResult.uncited` marks an answered question with no valid citation and `truncated` marks an answer cut by the output budget; both are for the caller and the evaluation suite (§62), neither changes the answer. References are one to three digits, so bracketed figures such as `[2024]` stay text                                                                                       |
| Rewriting (§26)       | `LLMQueryRewriter` rewrites only questions that have conversation history, with its own prompt (history as data, question last) and acceptance rule (non-empty, bounded, not an answer or refusal); it fails open on any provider error or `QUERY_REWRITE_TIMEOUT_SECONDS`. `QUERY_REWRITING=false` disables it. The user message stores the question as asked                                   |
| Conversation (OQ-8)   | Provisional: without a conversation id a conversation is created, titled after the question; an explicit document or collection scope wins, otherwise the conversation's collection scopes retrieval, otherwise every READY document. Earlier turns (bounded by `PROMPT_MAX_HISTORY_MESSAGES`) feed the rewriter and the prompt                                                                  |
| Persistence           | One transaction after generation: the new conversation (if any), the user message, the assistant message and its citations; the conversation's `updated_at` moves. Nothing is persisted when retrieval or generation fails, so a conversation never shows a question without its answer (OQ-18, provisional)                                                                                     |
| Fake model            | `FakeLLMProvider` answers a grounded prompt by echoing the question with `[1]`, returns the insufficient-evidence sentence when no document was retrieved, and returns a rewriting prompt's question unchanged, so local development and CI exercise the whole path                                                                                                                              |

## Alternatives considered

- **Citing every context item.** Over-cites and defeats §23 ("the chunks actually used");
  numbered references cost nothing extra and are validated server-side.
- **Post-hoc attribution by text matching.** Approximate and expensive; kept as a possible
  evaluation-side check (§62), not as the citation mechanism.
- **Persisting the user message before generation.** Would leave orphaned questions on
  provider failure; a retryable 503 with nothing persisted is simpler for the client and the
  evaluation suite. Streaming (§44) will revisit this (OQ-18).
- **Always rewriting the question.** A standalone question gains nothing and pays a model
  call; only follow-ups are rewritten.

## Consequences

- New settings `QUERY_REWRITING`, `QUERY_REWRITE_TIMEOUT_SECONDS`, `CITATION_MAX_QUOTE_CHARACTERS`.
- Generation metadata (model, usage, timing) is returned, not yet persisted; the usage ledger
  (§38) records it next. The reranker port reports latency but no provider usage units; a hosted
  reranker adapter adds them to `RerankingReport` when the vendor is chosen (OQ-17).
- Review (2026-09-22): the domain and application layers import no LangChain, vendor SDK or
  HTTP symbol (enforced by the architecture test); the only provider-shaped choices are the
  chat-completions field names inside the OpenAI-compatible adapter (`max_tokens`, which newer
  OpenAI models replace with `max_completion_tokens`; the adapter is updated when a model needs
  it) and the default `LLM_MODEL` value. The chat endpoints (§33) and streaming (§44) build on `RagService.answer`.
