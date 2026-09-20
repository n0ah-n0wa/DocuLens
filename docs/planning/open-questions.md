# DocuLens — Open Questions: Specification Ambiguities and Contradictions

**Date:** 2026-09-19
**Status:** Unresolved. Nothing below has been decided or implemented.
**Rule (§79, §91):** none of these may be resolved silently. Each needs an explicit decision, and any
decision that changes architecture must be recorded as an ADR and, where the spec is contradicted,
reflected back into `SPECIFICATIONS.md`.

Each item gives: spec references, the problem, options, and a _proposed default_. The proposed default is a
recommendation only and is **not applied** until confirmed.

Severity: **Blocking** = changes architecture or schema, must be decided before the referenced phase.
**Important** = changes behaviour or API contract. **Minor** = tooling or wording.

---

## Blocking

### OQ-1 — ChromaDB has no hosting target in a Lambda-only AWS design

**Refs:** §5.3, §5.4, §16, §57, §88 (Infrastructure checklist lists "ChromaDB").
**Problem:** §5.4 lists only serverless/managed compute (Lambda, API Gateway). ChromaDB is not an AWS-managed
service and its embedded mode needs a persistent, single-writer local filesystem, which Lambda does not
provide. The spec never says where Chroma runs in staging/production.
**Options:**
A. Chroma server on ECS Fargate (or EC2) with EBS/EFS, reached from Lambda inside the VPC — adds a compute
type not in §5.4.
B. Chroma Cloud (hosted) — adds an external SaaS dependency and secret.
C. Embedded Chroma on EFS mounted into Lambda — SQLite locking under concurrent workers makes this unsafe.
D. Replace Chroma with pgvector — contradicts the hard requirement in §5.1/§5.3 (would need a spec change).
**Proposed default:** A, with the `VectorStore` port (§18) keeping D possible later. Requires updating §5.4
and an ADR-002 amendment.

### OQ-2 — Worker compute vs. Lambda limits, and the container base image

**Refs:** §10.2 (50 MB / 500 pages), §48, §56, §65, §71 (`services/document-worker`).
**Problem:** §48 permits "Lambda-triggered workers ... or another AWS-native mechanism". Lambda caps a single
invocation at 15 minutes and 10 GB memory/ephemeral storage. A 500-page document with external embedding
calls may exceed that. Separately, §56 suggests distroless images, but Lambda container images need the
Lambda Runtime Interface Client, which distroless does not ship; the API and worker images may therefore need
different base-image strategies.
**Options:**
A. Lambda container image worker fed by SQS, with the pipeline split into resumable stages (each stage a
separate message) so no single invocation nears 15 min.
B. ECS Fargate worker service consuming SQS — no time limit; pairs naturally with OQ-1 option A.
C. Step Functions orchestrating stage Lambdas.
**Proposed default:** A if OQ-1 chooses B/C; B if OQ-1 chooses A (one ECS cluster for both). Base image:
`public.ecr.aws/lambda/python` for Lambda targets, distroless for ECS targets; document in ADR-006/008.

### OQ-3 — Upload path: payload caps vs. 50 MB default, and validation-before-storage ordering

**Refs:** §10.1, §10.2, §11, §12, §33 (`POST /api/v1/documents`), §7.3 states.
**Problem:** API Gateway limits request bodies to 10 MB and synchronous Lambda payloads to 6 MB. A direct
multipart `POST /documents` therefore cannot accept the 50 MB default. The alternative, presigned S3 upload,
means the file reaches S3 _before_ the server can validate MIME/signature/PDF validity, which inverts the
§12 order (Validation → S3 persistence). The §7.3 state machine (UPLOADED → VALIDATING) is compatible with
validate-after-store, so the spec is internally inconsistent on ordering.
The same limits constrain the **download** path: §24 requires opening the document and navigating to
the cited page, so the PDF must reach the browser either through the API (subject to the same
payload cap) or through a short-lived presigned S3 or CloudFront URL. The decision covers both
directions.
**Options:**
A. Presigned PUT to a quarantine prefix; `POST /documents` creates the row (UPLOADED) and returns the URL;
a completion call or S3 event triggers VALIDATING in the worker; invalid files are deleted from quarantine.
B. Direct upload through the API with `MAX_FILE_SIZE_MB` effectively capped at ~5 MB (spec default becomes
unreachable).
C. Direct upload via an ECS-hosted API (contradicts §5.4 Lambda).
**Proposed default:** A. Requires amending §12's ordering text, adding a completion endpoint to §33, and
verifying in the presigned policy: content-length-range and content-type.

### OQ-3b — Streaming (SSE) through API Gateway

**Refs:** §5.4, §6, §40 ("streaming or progressive ... where supported"), §44 ("should support", SSE preferred).
**Problem:** API Gateway has historically not streamed Lambda responses; streaming required a Lambda Function
URL in RESPONSE_STREAM mode (optionally fronted by CloudFront), or WebSockets. Whether current API Gateway
REST APIs support Lambda response streaming must be verified against AWS documentation at implementation
time. If not, "API Gateway" (§5.4) and "SSE" (§44) cannot both hold for the chat endpoint.
**Options:**
A. Serve only the streaming chat endpoint through a Lambda Function URL behind CloudFront; everything else
through API Gateway.
B. No true streaming: synchronous answer with progressive UI (polling/job status). Satisfies §40's
"where supported" but weakens §44's "should".
C. WebSocket API Gateway route for chat.
**Proposed default:** Build the RAG service as an async generator (transport-agnostic), expose SSE on
FastAPI, and pick A vs. B in ADR-008 after checking current AWS capabilities.

### OQ-7 — Hard delete (§31) vs. `DELETING`/`DELETED` states (§7.3) and citation retention

**Refs:** §7.3, §7.8, §28, §31 ("citations where appropriate"), §69.
**Problem:** §31 says deletion removes PostgreSQL metadata, yet §7.3 defines `DELETING` and `DELETED`
document states, which imply the row survives as a tombstone. §31 also leaves citation handling to "where
appropriate" while §28 makes messages immutable; a hard-deleted document leaves citations dangling.
**Options:**
A. Soft-delete tombstone: document row kept with status `DELETED` and all content (pages, chunks, vectors,
S3 object) purged; citations keep `document_id` and render "source deleted".
B. Hard delete everything; citation FK `ON DELETE SET NULL` with a copied `filename` snapshot.
C. Hard delete with cascade to citations (breaks message immutability semantics).
**Proposed default:** A (matches the state machine and makes the deletion saga observable/resumable), with
a periodic purge of old tombstones documented under §69.

### OQ-8 — Conversation scope model and nullability of `collection_id`

**Refs:** §7.3, §7.6, §27, §30.
**Problem:** §27 requires questions across a single document, a whole collection, or multiple selected
documents. The `Conversation` entity (§7.6) carries only `collection_id`; there is no representation of a
document-scoped or multi-document-scoped conversation. It is also unstated whether `collection_id` on
`Document` and `Conversation` may be null (documents can be "removed" from collections per §30).
**Options:**
A. Add a `conversation_documents` join table (scope = explicit document set); `collection_id` nullable on
both entities; empty set + collection → whole collection.
B. Scope per message (each question carries a document-id list) — simpler schema, weaker UX.
C. Restrict conversations to collections only (contradicts §27).
**Proposed default:** A. Retrieval always resolves scope → set of document ids → `user_id` + `document_id`
metadata filter.

### OQ-17 — LLM, embedding and reranker providers are unspecified

**Refs:** §3 (no self-hosted models), §15, §19, §21, §26, §38, §73, §78 (ADR-004/005), §87 (local without prod infra).
**Problem:** The spec names no provider for any of the three abstractions, yet cost tracking (§38) needs
per-model pricing, token counting (§7.5) needs a tokenizer, and local development must run without cloud.
Reranking options (cross-encoder in-process, hosted rerank API, LLM-as-reranker) have very different
latency/cost profiles.
**Options:** Amazon Bedrock (stays inside the AWS/IAM story, no extra secret) · Anthropic API · OpenAI API ·
hosted rerank API vs. in-process cross-encoder vs. no reranker initially.
**Proposed default:** Decide before Phase 4/5. Regardless of choice: a deterministic fake provider for all
three ports is mandatory for CI, and provider selection is config-driven. If Anthropic is chosen, consult the
Claude API reference skill at implementation time rather than relying on remembered model ids/pricing.

### OQ-19 — Frontend hosting/rendering mode and token storage

**Refs:** §5.4 (CloudFront/Route 53 "where appropriate"), §40 (session handling), §53 (XSS, CSRF "where applicable").
**Problem:** No hosting target is named for the Next.js app. Static export to S3+CloudFront rules out SSR
and route handlers (so no server-side cookie/BFF token handling); Lambda-hosted SSR or Vercel keep them.
Token storage (httpOnly cookies → CSRF defenses needed; in-memory access token + httpOnly refresh cookie;
localStorage → XSS exposure) is unspecified and drives the API's cookie/CORS design.
**Proposed default:** S3 + CloudFront static export; access token held in memory, refresh token in an
httpOnly, SameSite=Strict cookie scoped to the refresh endpoint; CORS locked to the frontend origin.
Requires the API to support cookie-based refresh, so decide before Phase 2.

---

## Important

### OQ-4 — Keyword retrieval backend for hybrid search

**Refs:** §18 ("where practical"), §7.5 (chunk text lives in PostgreSQL).
**Options:** PostgreSQL full-text search (tsvector/GIN) over chunks with score fusion · Chroma
`where_document $contains` (substring, no ranking) · in-memory BM25 (does not scale per user).
**Proposed default:** PostgreSQL FTS + reciprocal-rank fusion, behind the `Retriever` port; feature-flagged.

### OQ-5 — "Reprocess" vs. "re-index" semantics and endpoints

**Refs:** §29 (lists both), §32, §33 (only `/reprocess`).
**Problem:** Two verbs, one endpoint, no definitions.
**Proposed default:** `reprocess` = full pipeline from the S3 original (new extraction); `reindex` = re-chunk
and re-embed from stored pages (skips extraction). Both idempotent, both replace vectors by deterministic id.
Add `POST /documents/{id}/reindex` to §33 or fold into a `mode` body parameter.

### OQ-6 — Duplicate upload policy

**Refs:** §7.3 `content_hash`, §49, §64 ("duplicate uploads" adversarial test).
**Problem:** Behaviour on a duplicate is undefined: reject, return the existing document, or allow (e.g., in a
different collection). Uniqueness scope (per user? per collection?) is undefined.
**Proposed default:** Unique on `(owner_id, content_hash)` for non-deleted documents; API returns 409 with
the existing document id; cross-user duplicates are allowed (no cross-user linkage, for privacy).

### OQ-9 — Collection deletion: destination of contained documents

**Refs:** §7.3, §30.
**Problem:** "must not automatically delete its documents unless explicitly specified" — but where do they go?
**Proposed default:** `DELETE /collections/{id}` detaches documents (`collection_id = NULL`);
`?delete_documents=true` cascades via the document deletion saga. Depends on OQ-8 nullability.

### OQ-10 — "Search documents" (§29) vs. "search documents semantically" (§2 goal 7)

**Refs:** §2.1(7), §29, §33 (no search endpoint).
**Problem:** Two different features share one phrase. Metadata search (filename/collection) is a list filter;
semantic search returns ranked chunks without LLM generation.
**Proposed default:** `GET /documents?q=` for metadata search; `POST /search` (scope + query → ranked chunks
with citations metadata) for semantic search, reusing the retrieval pipeline. Both added to §33.

### OQ-11 — Overlapping limit/quota names, units, and the cost pricing table

**Refs:** §10.2 (`MAX_DOCUMENTS_PER_USER`, `MAX_PAGES_PER_DOCUMENT`), §39 (`MAX_DOCUMENTS`, `MAX_PAGES`,
`MAX_STORAGE`, `MAX_AI_COST_PER_DAY`), §38.
**Problem:** `MAX_DOCUMENTS` vs `MAX_DOCUMENTS_PER_USER` name the same limit; `MAX_PAGES` is ambiguous
(per document vs per user total); `MAX_STORAGE` has no unit; `MAX_AI_COST_PER_DAY` needs a per-model price
table whose location (config vs code) is unspecified.
**Proposed default:** One `QUOTA_*` namespace: `QUOTA_MAX_DOCUMENTS`, `QUOTA_MAX_PAGES_TOTAL`,
`QUOTA_MAX_STORAGE_MB`, `QUOTA_MAX_QUESTIONS_PER_DAY`, `QUOTA_MAX_AI_COST_USD_PER_DAY`; per-document limits
under `UPLOAD_*`; model prices in config (`AI_PRICING_JSON`), never hardcoded.

### OQ-12 — JWT library and access-token revocation on logout

**Refs:** §5.1 ("python-jose or equivalent"), §8.
**Problem:** python-jose has had slow maintenance and CVEs (2024); PyJWT is the actively maintained equivalent.
§8 requires refresh-token revocation only; whether logout must also invalidate the still-valid access token
(Redis denylist by `jti`) is unstated.
**Proposed default:** PyJWT; short access lifetime (≤15 min) without denylist, refresh revocation only.
Denylist deferred unless required.

### OQ-14 — Pages without extractable text; wholly empty PDFs

**Refs:** §13, §64 ("empty PDFs").
**Problem:** "the system must record this condition" — where (page metadata, document field) and what
happens to a document with zero extractable text (READY with zero chunks vs FAILED)?
**Proposed default:** `DocumentPage.metadata.has_text = false` per page, `Document.metadata.empty_page_count`;
zero-text documents become `READY` with `chunk_count = 0` and a user-visible warning; not FAILED (the file
is valid).

### OQ-18 — Partial streaming failures and SYSTEM messages

**Refs:** §7.7 (roles include SYSTEM; no status field), §28 (immutable), §44, §68.
**Problem:** If generation fails mid-stream, the `Message` entity has no field to mark a partial/failed
assistant response. It is also unstated whether the system prompt is persisted as a SYSTEM message per
conversation (§68 discourages storing prompts unnecessarily).
**Proposed default:** Add `status` (COMPLETE | PARTIAL | FAILED) and `metadata` to `Message`; persist partial
text with `status=PARTIAL` and no citations; do not persist system prompts (keep `SYSTEM` role reserved).

### OQ-21 — OpenTelemetry exporter and metrics mechanism on Lambda

**Refs:** §51, §52, §5.4 (CloudWatch).
**Problem:** "expose or collect" metrics — a Prometheus endpoint does not fit Lambda. OTel export target is unnamed.
**Proposed default:** CloudWatch Embedded Metric Format via structured logs for metrics; OTel traces via the
ADOT Lambda layer to X-Ray (console exporter locally).

### OQ-23 — How Alembic migrations run in CD

**Refs:** §45 (Alembic exclusively), §60, §87 (RDS in private subnets).
**Problem:** GitHub-hosted runners cannot reach a private RDS. The migration execution mechanism is unspecified.
**Options:** dedicated migration Lambda invoked by the deploy workflow · one-off ECS task · SSM-tunnelled runner.
**Proposed default:** Migration Lambda (same image as the API) invoked synchronously by the CD job before
traffic shift; failure blocks the deploy.

---

## Minor

### OQ-13 — Local S3-compatible storage and local queue

**Refs:** §61 (S3-compatible), §87. **Options:** MinIO vs LocalStack; Redis-list vs in-process queue.
**Proposed default:** MinIO (lighter) and a Redis-backed local queue adapter; LocalStack only if SQS/Secrets
Manager fidelity is needed in integration tests.

### OQ-15 — Tooling: package managers; root `tests/` vs per-app tests

**Refs:** §5.1 (pinning), §71. **Proposed default:** `uv` with lockfile for Python, `pnpm` workspaces for Node;
tests live next to each app (`apps/api/tests`, `apps/web/e2e`); root `tests/` not used.

### OQ-16 — Tokenizer for `token_count`

**Refs:** §7.5, §14, §20. **Problem:** token counts are model-specific; provider unknown (OQ-17).
**Proposed default:** tokenizer chosen by the embedding provider adapter; the domain stores counts as opaque
integers plus the tokenizer name in chunk metadata so re-indexing can detect drift.

### OQ-20 — Frontend unit test framework

**Refs:** §5.2 (only Playwright listed), §59 ("Frontend tests").
**Proposed default:** Playwright for E2E; add `vitest` only for non-trivial pure logic (query builders,
citation formatting). Otherwise type-check + lint + E2E are the frontend gates.

### OQ-22 — RAG evaluation cadence and CI gating

**Refs:** §62, §63. **Problem:** real-provider evaluation costs money; gating every PR on it is impractical.
**Proposed default:** Fake-provider retrieval/citation checks on every PR; full evaluation with real
providers on a manual/nightly workflow with results committed to `docs/eval/`.

### OQ-24 — Scope clarifications not covered by the spec

**Refs:** §7.1 (`DELETED` user status), §8, §28 ("administrative correction"), §69.
**Items:** account deletion endpoint (implied by user status but absent from §33 and §69); email
verification and password reset (absent from §8); an admin role (implied by §28, never defined).
**Proposed default:** Out of scope for v1 unless confirmed; `DELETED` status reserved; no admin role.
**Security review note (2026-09-20):** registration answers 409 for a known email (§33), which
reveals account existence; the neutral alternative needs email verification, so it waits on this
question. Until then the per-address rate limit bounds enumeration speed (ADR-007).

### OQ-25 — LangChain scope

**Refs:** §5.1 (required), §72 (domain independence), §73 ("may be used as integration layer"), §84.
**Problem:** LangChain is simultaneously "required" and merely "may be used"; a full `langchain` install adds
a large dependency surface that §84 discourages.
**Proposed default:** Use only `langchain-core`, `langchain-text-splitters` and one provider package inside
`infrastructure/`; nothing LangChain-typed crosses a port. Record in ADR-001.

### OQ-26 — Terraform and the `local` environment

**Refs:** §57 ("Environment separation: local / staging / production must be supported" under IaC).
**Proposed default:** `local` is docker compose only; Terraform has `staging` and `production` roots.
Documented in ADR-008.

---

## Added during the architecture review (2026-09-19)

### OQ-27 — Consistency between the document row and the processing job (Important)

**Refs:** §12, §46, §48, §49, §66.
**Problem:** the PostgreSQL commit and the queue enqueue cannot share a transaction; whichever
happens first can succeed while the other fails, leaving either an orphaned `UPLOADED` row or a job
for a missing row. The spec demands idempotency and recoverability but not the mechanism.
**Options and recommendation:** [ADR-011](../decisions/ADR-011-job-enqueue-consistency.md)
(commit-then-enqueue with a reconciliation sweep, transactional outbox, or tolerant worker).

### OQ-28 — Database connection management for Lambda (Important)

**Refs:** §5.4, §45, §65, §66.
**Problem:** each concurrent Lambda instance holds its own database connections; under load the
total can exceed the RDS connection limit. §5.4 names no connection-management component.
**Options and recommendation:** [ADR-012](../decisions/ADR-012-lambda-database-connections.md)
(RDS Proxy, reserved concurrency with tiny pools, or Aurora Data API).

### OQ-29 — Where the insufficient-evidence outcome is decided (Important)

**Refs:** §21, §63, §67.
**Problem:** §21 requires an explicit "not enough information" response instead of fabrication, but
does not say whether that is decided before the LLM call (retrieval-score threshold, saving cost and
removing the model from the decision), by the LLM under prompt instruction, or by both. The choice
changes cost, latency and how the evaluation suite (§63) scores the "answer does not exist" cases.
**Proposed default:** both — a configurable score threshold short-circuits clearly empty retrievals,
and the prompt instructs the model for the remaining cases; the evaluation suite measures each path.

### OQ-30 — Citation attribution mechanism (Important)

**Refs:** §7.8, §23, §62 (citation correctness).
**Problem:** citations must reference the chunks actually used for the answer, yet the spec does not
say how the system knows which context chunks the model relied on. Options: the model must emit
explicit references to numbered context items (structured output, verifiable, provider-dependent);
every chunk that survived reranking is cited (simple, over-cites); or a post-hoc attribution step
matches answer sentences to chunks (extra cost, approximate).
**Proposed default:** numbered context items with mandatory explicit references, validated
server-side (unknown references are dropped and counted as a citation error in evaluation), with
the fallback of citing all surviving chunks when the provider cannot follow the format.

## Decision log

| OQ                                                                                                                                                                                                        | Decision                                                                                                                                                                                                                                                                                                                                                                     | Date       | ADR     |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------- |
| OQ-13                                                                                                                                                                                                     | Deferred. The compose stack provides PostgreSQL, Redis and ChromaDB only; the local S3-compatible store is chosen when the storage phase starts.                                                                                                                                                                                                                             | 2026-09-19 | —       |
| OQ-15                                                                                                                                                                                                     | Provisional (Phase 0, reversible): uv workspace with one lockfile, pnpm workspace, tests per package, root `tests/` unused.                                                                                                                                                                                                                                                  | 2026-09-19 | ADR-010 |
| OQ-20                                                                                                                                                                                                     | Provisional (Phase 0): Playwright only; a unit runner is added when pure frontend logic exists.                                                                                                                                                                                                                                                                              | 2026-09-19 | ADR-010 |
| OQ-26                                                                                                                                                                                                     | Provisional (Phase 0): `local` is docker compose; Terraform roots exist for staging and production only.                                                                                                                                                                                                                                                                     | 2026-09-19 | —       |
| OQ-7                                                                                                                                                                                                      | Provisional (persistence phase, reversible): the schema keeps the `DELETING`/`DELETED` states, cascades owned content (pages, chunks, messages, citations), sets `citations.chunk_id` to NULL when a chunk is re-indexed, and refuses to hard-delete a cited document (`ON DELETE RESTRICT`). No deletion use case exists yet; tombstoning versus hard delete is still open. | 2026-09-20 | —       |
| OQ-8                                                                                                                                                                                                      | Provisional (persistence phase): `collection_id` is nullable on documents and conversations because §30 allows detaching a document. How a conversation scopes several selected documents is still open; a join table can be added without changing existing rows.                                                                                                           | 2026-09-20 | —       |
| OQ-16                                                                                                                                                                                                     | Provisional (ADR-003): a deterministic, dependency-free `regex-v1` tokenizer bounds chunk sizes and counts tokens; the embedding provider's tokenizer replaces it behind the `Tokenizer` port, and every chunk records the tokenizer name.                                                                                                                                   | 2026-09-20 | ADR-003 |
|  one live document per `(owner_id, content_hash)`, enforced by a partial unique index; a duplicate answers 409 `DUPLICATE_DOCUMENT` with the existing id; the same content may exist for different users. | 2026-09-20                                                                                                                                                                                                                                                                                                                                                                   | ADR-015    |
| OQ-14                                                                                                                                                                                                     | Provisional (ADR-015): pages without text are stored with `has_text = false` and counted in `documents.metadata.extraction`; a valid PDF whose pages all lack text still completes extraction. `documents.metadata` (JSONB) is added beyond the §7.3 field list to hold PDF and extraction metadata.                                                                         | 2026-09-20 | ADR-015 |
| OQ-13                                                                                                                                                                                                     | Decided (ADR-014): MinIO (`quay.io/minio/minio`, pinned) is the local S3-compatible store, in docker compose and via testcontainers; a filesystem adapter serves Docker-free development and unit tests. The local queue adapter remains open.                                                                                                                               | 2026-09-20 | ADR-014 |
| OQ-12                                                                                                                                                                                                     | Decided (ADR-007): PyJWT with HS256; access tokens live 15 minutes by default and are not denylisted; refresh tokens are durable rows rotated on every use, revoked on logout, and reuse of a rotated token revokes the whole session family.                                                                                                                                | 2026-09-20 | ADR-007 |
