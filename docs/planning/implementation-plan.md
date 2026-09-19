# DocuLens — Implementation Plan (planning note)

**Date:** 2026-09-19
**Status:** Planning only. No application code exists yet.
**Source of truth:** `SPECIFICATIONS.md` v1.0 (§ references below point to its sections).
**Companion:** `open-questions.md` — items tagged `[OQ-n]` are unresolved spec ambiguities or
contradictions. They are NOT resolved here; each affected phase must not start until its OQs are decided.

This note is temporary. Once decisions are taken, the durable outcome belongs in `docs/adr/` and
`docs/architecture/`, and this file should be deleted.

---

## 1. What the specification requires

### 1.1 Architecture (§4, §6, §72–§74)

- Strict layering: `interfaces → application → domain ← infrastructure`.
  Domain must not import FastAPI, AWS SDK, ChromaDB, LangChain or SQLAlchemy internals (§72).
- Ports (Protocols) at the domain/application boundary:
  `LLMProvider`, `EmbeddingProvider`, `RerankerProvider` (§73), `VectorStore`/`Retriever` (§18),
  `ObjectStorage` (§11), `JobQueue` (§48), repositories, `RateLimiter`, `UsageTracker`.
- `RAGService.answer_question()` orchestrating independently testable components:
  QueryRewriter → Retriever → Reranker → ContextBuilder → PromptBuilder → LLM → CitationBuilder (§74).
- Document processing runs outside the API request (§6, §12, §48, §65): a separate worker service.
- PostgreSQL is the only source of truth; Redis is transient; S3/Chroma are non-transactional
  side systems requiring compensation/retry (§45–§47).

### 1.2 Technology stack (§5)

| Area        | Required                                                                                                                                   | Recommended / supporting                                                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| Backend     | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, LangChain, PyMuPDF, ChromaDB                                                  | httpx, tenacity, structlog, python-jose _or equivalent_, argon2-cffi, pytest, pytest-asyncio, testcontainers, ruff, mypy |
| Frontend    | Next.js, TypeScript (strict), React, Tailwind CSS                                                                                          | TanStack Query, React Hook Form, Zod, Playwright                                                                         |
| Persistence | PostgreSQL, ChromaDB, Amazon S3, Redis                                                                                                     | —                                                                                                                        |
| AWS         | Lambda, API Gateway, S3, RDS/Aurora PostgreSQL, ElastiCache Redis, CloudWatch, IAM, Secrets Manager; CloudFront/Route 53 where appropriate | —                                                                                                                        |
| IaC         | Terraform (preferred)                                                                                                                      | —                                                                                                                        |
| CI/CD       | GitHub Actions, Docker, OIDC to AWS                                                                                                        | —                                                                                                                        |

All versions must be pinned (§5.1). Dependencies outside this list need explicit justification (§84) — see §5 of this note.

### 1.3 Environments (§57, §87)

`local` (docker compose, no cloud dependencies), `staging`, `production` (Terraform-managed, approximating each other).

### 1.4 Quality gates on every PR (§59, §81, §82)

Formatting · Lint · Type check · Unit · Integration · Frontend tests · Build · Security checks
(Python deps, npm deps, container image, secrets) · Infrastructure validation. Pipeline fails on any violation.

### 1.5 Security requirements (§8–§11, §22, §37–§39, §53–§58, §68)

- Argon2id password hashing; JWT access (short) + refresh (long, rotated, revocable) with iss/aud/sub/iat/exp/jti.
- Ownership enforced server-side on every user-owned resource; no IDOR via enumeration.
- Upload validation: extension, MIME, magic bytes, size, PDF parse validity; generated S3 keys only.
- Prompt injection: retrieved text is data; three-part prompt separation; adversarial tests mandatory.
- Rate limiting (auth, upload, questions, AI ops) and per-user quotas, server-side, configurable.
- Secrets only via env/Secrets Manager; `.env.example` documented; nothing sensitive logged.
- Least-privilege IAM, separate roles (API, worker, deploy, monitoring); non-root minimal images.

### 1.6 Testing strategy (§61–§64)

Unit (domain, chunking, authz, rewriting, prompts, citations, quotas) · Integration (Postgres, Chroma, Redis,
S3-compatible, processing; testcontainers) · API (auth, authz, validation, errors, lifecycles) · E2E Playwright
(register → upload → process → ask → inspect citation) · RAG evaluation with curated dataset · hallucination
suite · adversarial suite (malicious/corrupt/empty/huge PDFs, injection, duplicates, expired tokens, cross-user).

### 1.7 Deployment targets (§5.4, §48, §56–§60)

API Gateway → Lambda (FastAPI) · SQS-driven worker · RDS PostgreSQL · ElastiCache Redis · S3 · ChromaDB
(hosting undefined → `[OQ-1]`) · CloudWatch · Secrets Manager · IAM · CD: build → test → scan → staging →
smoke → manual approval → production.

---

## 2. Target repository layout

Follows §71 with one adaptation (recorded in ADR-001): the framework-free core
(domain/application/infrastructure) is its own package, `packages/core` (import name `doculens`);
`apps/api` and `services/document-worker` are thin interface packages that depend on it. Packaging
then enforces the §72 boundary: the core cannot import FastAPI because it does not depend on it.
Implemented in Phase 0 (2026-09-19).

```text
doculens/
├── packages/
│   ├── core/                      # doculens-core: the hexagonal core (no web framework)
│   │   ├── src/doculens/
│   │   │   ├── domain/            # entities, value objects, state machines, ports, domain errors
│   │   │   ├── application/       # use cases: auth, collections, documents, processing, rag, conversations, quotas, usage
│   │   │   └── infrastructure/    # sqlalchemy/, s3/, chroma/, redis/, ai/{fake,<provider>}, queue/, telemetry/, security/
│   │   ├── tests/{unit,integration}/
│   │   └── pyproject.toml
│   └── shared-types/              # TS types generated from the FastAPI OpenAPI schema (no hand-written duplication)
├── apps/
│   ├── api/                       # doculens-api: FastAPI interface layer
│   │   ├── src/doculens_api/
│   │   │   ├── routers/           # versioned routes under /api/v1, probes under /health
│   │   │   ├── config.py          # pydantic-settings, validated at startup (§70) — Phase 1
│   │   │   ├── main.py            # FastAPI app factory
│   │   │   └── lambda_handler.py  # ASGI→Lambda adapter entrypoint — Phase 9
│   │   ├── alembic/               # Phase 1
│   │   ├── tests/{api,adversarial}/
│   │   └── pyproject.toml
│   └── web/                       # Next.js (strict TS, Tailwind, TanStack Query, RHF + Zod), e2e/ (Playwright)
├── services/
│   └── document-worker/           # doculens-worker: queue consumer over the core; compute target per OQ-2
├── evals/                         # RAG evaluation dataset + harness (separate from pytest gates, §62)
├── infrastructure/terraform/
│   ├── modules/{networking,compute,database,storage,cache,iam,secrets,observability,api-gateway,vector-store}
│   └── envs/{staging,production}
├── docker/                        # api.Dockerfile, worker.Dockerfile, compose.yaml (postgres, redis, chroma, s3-compatible)
├── docs/{architecture,adr,eval,planning}/ + deployment.md + security.md
├── scripts/                       # dev helpers only (no business logic)
├── .github/workflows/             # ci.yml, cd-staging.yml, cd-production.yml, security.yml, rag-eval.yml
├── SPECIFICATIONS.md · README.md · .env.example
```

---

## 3. Implementation phases

Every phase ends with the §81 checkpoint: tests · lint · type check · build · review · commit(s).
Commits follow §85 prefixes. Each phase produces or updates the ADR(s) listed.

### Phase 0 — Repository foundation

- Monorepo tooling: Python package manager with lockfile (proposed: `uv`; alternative `pip-tools`) and
  Node workspaces (proposed: `pnpm`). Both are dev tools, not runtime deps. → confirm in `[OQ-15]`.
- `ruff` (format + lint), `mypy --strict`, strict `tsconfig`, ESLint/Prettier for web.
- `.env.example` (all §70 categories), `.gitignore` excluding `.env*`, `docker/compose.yaml` with
  Postgres, Redis, Chroma, S3-compatible store (`[OQ-13]`).
- `docs/adr/` template + ADR-001 (backend architecture), README skeleton, CI skeleton that runs the gates
  even on an empty repo so the pipeline is green from commit one.
- **Gate:** CI green; compose stack starts.

### Phase 1 — Backend skeleton

- `config.py`: pydantic-settings grouped by §70 categories; production profile rejects unsafe defaults at startup.
- structlog JSON logging with §50 fields; request-ID middleware (header in, header out, log context) (§36).
- Uniform error envelope `{error:{code,message,request_id}}` + exception handlers; no internal details leak (§35).
- SQLAlchemy 2.x async engine/session, Alembic configured, base ORM mixins (uuid PK, timestamps).
- Domain entities + state machines for `User`, `Collection`, `Document`, `DocumentPage`, `DocumentChunk`,
  `Conversation`, `Message`, `Citation` (§7) with §45 indexes and FKs → first Alembic migration.
- Health endpoints (`/health/live`, `/health/ready`); OpenAPI metadata (§75).
- **Tests:** unit (config validation, state machine transitions), API (error envelope, request-id).
- **Depends on:** `[OQ-7]` (hard vs. soft delete), `[OQ-8]` (conversation scope model) — both affect the schema.

### Phase 2 — Authentication & authorization

- Argon2id via `argon2-cffi`; password policy validator; account status checks (§8).
- JWT access/refresh with all §8 claims; refresh tokens stored hashed with `jti`, rotated on use,
  revoked on logout and on reuse detection. Library choice → `[OQ-12]`.
- Endpoints: register, login, refresh, logout, `GET /users/me` (§33). Rate limiting on auth (§37) via Redis.
- `CurrentUser` dependency + ownership helper used by every later router (§9).
- **Tests:** unit (token claims, rotation, hashing), API (expired/rotated/reused tokens, suspended accounts),
  adversarial (cross-user attempts scaffolded as a reusable fixture).
- **ADR:** ADR-007 Authentication.

### Phase 3 — Collections, documents, upload, storage, quotas

- Collections CRUD with ownership; deletion semantics for contained documents → `[OQ-9]`.
- Document metadata CRUD: list/filter/search(metadata) (`[OQ-10]`), rename, move, delete (§29).
- Upload flow → `[OQ-3]` (direct multipart through API Gateway/Lambda is capped well below the 50 MB default;
  presigned S3 upload changes where validation happens vs. §12 ordering).
- Validation chain: extension → MIME → magic bytes → size → PyMuPDF open/page count (§10). Content hash;
  duplicate policy → `[OQ-6]`.
- `ObjectStorage` port + S3 adapter (generated keys `documents/{user_id}/{document_id}/original.pdf`, §11);
  local adapter target → `[OQ-13]`.
- Quota service (§39) with unified config names → `[OQ-11]`; enforced before upload/question.
- **Tests:** unit (validators, quotas), integration (S3-compatible), API (lifecycle, authz), adversarial
  (unsupported types, oversized, spoofed extension, corrupted PDF).

### Phase 4 — Processing pipeline, worker, vector store, deletion, re-indexing

- `JobQueue` port: SQS adapter + local adapter (in-process or Redis list) so local runs without AWS (§87).
- Worker entrypoint (`services/document-worker`) → compute target `[OQ-2]`.
- Pipeline stages as separate units with explicit state transitions (§7.3, §12): validate → extract (PyMuPDF,
  page boundaries, empty-page recording `[OQ-14]`) → chunk (configurable, paragraph-aware, token counts
  `[OQ-16]`) → embed (`EmbeddingProvider`) → index (`VectorStore` with deterministic ids = chunk ids so
  upsert is idempotent, §32/§49) → metadata update → READY; any failure → FAILED with safe message + diagnostics.
- Retry with `tenacity` exponential backoff, bounded attempts; DLQ semantics (§48, §66).
- Chroma adapter: metadata `user_id, document_id, collection_id, page_number, chunk_id, chunk_index` (§16);
  every query filtered by `user_id`; single-collection vs per-user layout decided in ADR-002. Hosting → `[OQ-1]`.
- Deletion saga (§31): idempotent, ordered (vectors → S3 → rows), partial failure recorded and re-runnable.
  Reprocess vs. re-index semantics → `[OQ-5]`.
- Fake `EmbeddingProvider` (deterministic vectors) for tests/local.
- **Tests:** unit (chunker, state machine, idempotency), integration (Postgres + Chroma + S3 via testcontainers,
  full pipeline on sample PDFs), adversarial (empty, corrupted, huge, malicious PDFs).
- **ADRs:** ADR-002 vector DB, ADR-003 chunking, ADR-004 embeddings, ADR-006 async processing.

### Phase 5 — RAG, conversations, streaming, cost tracking

- `Retriever` port: vector retrieval + metadata filtering; keyword retrieval backend → `[OQ-4]`; scope
  (document / collection / selected documents) → `[OQ-8]`.
- `RerankerProvider` port with no-op default; concrete provider → `[OQ-17]`.
- `ContextBuilder`: deterministic ordering, max size/count, dedupe, source diversity (§20).
- `PromptBuilder`: SYSTEM / USER QUESTION / RETRIEVED CONTENT separation, grounding, insufficient-evidence
  and injection-resistance instructions (§21–§22).
- `QueryRewriter` (LLM-backed, original message preserved) (§25–§26).
- `CitationBuilder` mapping answer evidence to persisted `Citation` rows (§23).
- `LLMProvider` port + fake provider (scripted, deterministic) + concrete provider → `[OQ-17]`.
- Conversations/messages API (§28, §33); answer generation on `POST /conversations/{id}/messages`;
  streaming transport → `[OQ-3b]`; final message + citations persisted after stream; partial-failure
  handling → `[OQ-18]`.
- Usage/cost tracking per request (§38) and daily quotas (§39); pricing table location → `[OQ-11]`.
  Rate limiting on question endpoints (§37).
- **Tests:** unit for every component incl. prompt-injection corpus (§22), API (conversation lifecycle,
  scope enforcement, quota exhaustion), integration (end-to-end with fake providers).
- **ADR:** ADR-005 LLM provider.

### Phase 6 — Frontend

- Next.js app (strict TS, Tailwind); hosting/rendering mode → `[OQ-19]`; token storage → `[OQ-19]`.
- TanStack Query for server state; RHF + Zod forms; API client generated from OpenAPI into `packages/shared-types`.
- Screens (§40): auth, dashboard (documents, collections, status, recent conversations, usage), document view,
  chat with progressive rendering, citations panel with source preview and page navigation (§24).
- Explicit IDLE/LOADING/SUCCESS/ERROR/EMPTY states (§43); responsive (§41); WCAG practices (§42).
- **Tests:** Playwright E2E for the §61 critical path against the compose stack with fake AI providers;
  unit test runner for pure logic → `[OQ-20]`.

### Phase 7 — Observability

- OpenTelemetry tracing across HTTP → processing → retrieval → reranking → LLM → persistence (§52);
  exporter/backend → `[OQ-21]`. Metrics (§51) via a Lambda-friendly mechanism → `[OQ-21]`.
- Log field audit against §50/§68 (no secrets, no full document text).

### Phase 8 — RAG evaluation, hallucination and adversarial suites

- `evals/` dataset: questions × expected evidence × answer characteristics, covering §63 categories
  (direct, indirect, multi-document, absent).
- Harness computing retrieval relevance, context precision/recall, citation correctness, groundedness,
  answer relevance (§62). Runs on demand / nightly, not on every PR (cost) → confirm in `[OQ-22]`.
- **ADR:** ADR-009 RAG evaluation strategy.

### Phase 9 — Docker and Terraform

- Multi-stage Dockerfiles for API and worker: non-root, pinned, no secrets, health checks. Lambda base-image
  vs. distroless tension → `[OQ-2]`.
- Terraform modules (§57) and `envs/staging`, `envs/production`; least-privilege IAM roles (§58); GitHub OIDC
  role; Secrets Manager entries; CloudWatch alarms/log groups. Migration execution path → `[OQ-23]`.
- **ADR:** ADR-008 AWS deployment architecture.

### Phase 10 — CI/CD

- `ci.yml` on PR: format, lint, mypy, unit, integration (service containers), web lint/type/test/build,
  Docker build, `pip-audit`, `npm audit`, container scan, secret scan, `terraform fmt/validate` (+ lint).
- `cd-staging.yml` on merge to main: build → push ECR → deploy → migrations → smoke tests.
  `cd-production.yml`: manual approval environment → deploy → smoke. OIDC only, no static keys (§5.5).
- Branch protection documented (§86).

### Phase 11 — Documentation (continuous; finalized last)

README (§76 14 items), architecture docs with Mermaid (§77), ADR-001…009 plus ADRs for every OQ decision,
deployment guide, security doc, eval methodology, eventual-consistency/cleanup guarantees (§69).

---

## 4. Suggested increment order and checkpoints

```text
P0 foundation → P1 skeleton → P2 auth → P3 documents/upload → P4 pipeline/worker
→ P5 RAG/conversations → P6 frontend → P7 observability → P8 evaluation
→ P9 docker/terraform → P10 CI/CD hardening → P11 docs finalization
```

CI (P10) is started in P0 as a skeleton and extended in each phase; docs (P11) accrue per phase.

---

## 5. Dependencies that will be needed beyond the §5 list (each requires §84 justification)

| Need                                | Candidate                                                           | Why the spec list is insufficient                                          |
| ----------------------------------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Run FastAPI on Lambda               | `mangum` (ASGI adapter) or AWS Lambda Web Adapter (no Python dep)   | §5.4 mandates Lambda; FastAPI has no native handler                        |
| AWS APIs (S3, SQS, Secrets Manager) | `boto3`/`aiobotocore`                                               | §11, §48, §54 require them; not listed                                     |
| Settings                            | `pydantic-settings`                                                 | §70 validated config; separate package from Pydantic v2 core               |
| PostgreSQL async driver             | `asyncpg` or `psycopg[binary]`                                      | SQLAlchemy needs a driver                                                  |
| Redis client                        | `redis`                                                             | §37, §47                                                                   |
| Multipart parsing                   | `python-multipart`                                                  | FastAPI upload endpoints require it (only if `[OQ-3]` keeps direct upload) |
| Token counting                      | `tiktoken` or provider tokenizer                                    | §7.5 `token_count`; choice tied to `[OQ-16]`                               |
| Tracing                             | `opentelemetry-sdk` + instrumentations                              | §52                                                                        |
| JWT                                 | `PyJWT` (equivalent to python-jose)                                 | `[OQ-12]`                                                                  |
| AI provider SDK                     | depends on `[OQ-17]`                                                | §73                                                                        |
| LangChain scope                     | `langchain-core`, `langchain-text-splitters`, provider package only | §5.1 requires LangChain; §72/§84 push for minimal surface                  |
| Web unit tests                      | `vitest` (only if `[OQ-20]` says yes)                               | §59 "Frontend tests"                                                       |
| Dev tooling                         | `uv`/`pnpm`, `pip-audit`, `gitleaks`, `trivy`, `tflint`             | CI gates (§55, §59) — CI tools, not runtime deps                           |

Nothing is added in this planning step.

---

## 6. Risks to track (not spec contradictions)

- Lambda cold starts inside a VPC (needed for RDS/ElastiCache) vs. API p95 < 500 ms (§65); NAT Gateway
  needed for outbound LLM calls adds fixed cost. Mitigation candidates: provisioned concurrency, VPC endpoints.
- Lambda 15-minute ceiling for worst-case documents (500 pages, 50 MB + embeddings) → see `[OQ-2]`.
- PyMuPDF is AGPL-licensed (commercial license otherwise). Acceptable for a portfolio project; document in ADR.
- RAG evaluation and E2E with real providers incur cost; default to fake providers in CI.
- ChromaDB operational maturity for production; the `VectorStore` port (§18) is the hedge.
- GitHub OIDC bootstrap requires one manual, documented Terraform apply from a trusted machine.
