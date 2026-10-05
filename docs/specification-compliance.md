# Specification compliance audit

**Authority:** [`SPECIFICATIONS.md`](../SPECIFICATIONS.md) (§91).  
**Date:** 2026-10-03.  
**Method:** Independent requirement-by-requirement review of the repository (code, tests, CI,
Terraform, docs). Prior reviews were not trusted. Status labels below mean:

| Status              | Meaning                                                                            |
| ------------------- | ---------------------------------------------------------------------------------- |
| **IMPLEMENTED**     | Requirement met in code with meaningful automated tests (or N/A for pure process). |
| **PARTIAL**         | Substantial work exists; named gaps or open questions remain.                      |
| **NOT_IMPLEMENTED** | Missing or only aspirational.                                                      |
| **N/A**             | Intent / non-goal / process rule not “implemented” as a feature.                   |

**Overall verdict:** The **application** (auth, documents, hybrid RAG, citations, conversations,
quotas, Next.js UI, local/CI quality system) is largely real. **Live AWS staging/production** is
**not** demonstrated. Several specification items remain open via
[`docs/planning/open-questions.md`](planning/open-questions.md). Do **not** treat this checklist as
“production ready.”

**Related:** [`architecture.md`](architecture.md), [`api.md`](api.md), [`database.md`](database.md),
[`rag.md`](rag.md), [`security.md`](security.md), [`deployment.md`](deployment.md),
[`operations.md`](operations.md), [`development.md`](development.md), [`decisions/`](decisions/),
[`eval/methodology.md`](eval/methodology.md).

---

## Summary scoreboard

| Band            | Count (approx.) | Examples                                                                                                                     |
| --------------- | --------------: | ---------------------------------------------------------------------------------------------------------------------------- |
| IMPLEMENTED     |             ~45 | §4, §7–9, §11–15, §17–18, §20–22, §25–26, §28, §31–37, §43, §45–46, §48–50, §54–55, §59, §61–63, §66–67, §70–75, §83, §91    |
| PARTIAL         |             ~35 | §5–6, §10, §16, §19, §23–24, §27, §29–30, §38–42, §44, §47, §51–53, §56–58, §60, §64, §68–69, §76–79, §81–82, §84–85, §87–90 |
| NOT_IMPLEMENTED |              ~3 | §65 (measured p95), §86 (branch protection), live §88 production deploy                                                      |
| N/A             |              ~5 | §1 intent, §3 non-goals, §80 agent rules                                                                                     |

---

## §§1–40 Product, domain, API, frontend

| §   | Title                    | Status      | Implementation                                                          | Tests                                 | Security                | Docs                     | Gaps / deviations                                                                          |
| --- | ------------------------ | ----------- | ----------------------------------------------------------------------- | ------------------------------------- | ----------------------- | ------------------------ | ------------------------------------------------------------------------------------------ |
| 1   | Product overview         | N/A         | Intent realised across apps/packages/services                           | —                                     | —                       | architecture.md          | Completeness via later rows                                                                |
| 2   | Product goals            | PARTIAL     | Goals 1–6,8–15,17 largely present                                       | Per-feature suites                    | —                       | —                        | Semantic search UI/API weak (**OQ-10**); usage dashboard missing; PDF page jump (**OQ-3**) |
| 3   | Non-goals                | N/A         | Correctly absent (OCR-required, mobile app, fine-tuning, …)             | —                                     | —                       | SPEC §3                  | —                                                                                          |
| 4   | Target architecture      | IMPLEMENTED | Hexagonal `doculens` layers; thin API/worker                            | `test_architecture.py`                | Ports isolate infra     | ADR-001                  | —                                                                                          |
| 5   | Technology stack         | PARTIAL     | FastAPI, SQLAlchemy 2, Next, PG, S3, Redis, Chroma client, pinned locks | CI                                    | —                       | ADR-010                  | **LangChain not used** (**OQ-25**); README previously claimed it (fixed)                   |
| 6   | High-level architecture  | PARTIAL     | Next → API → PG/Redis/S3/Chroma/LLM; async jobs                         | async/job tests                       | —                       | architecture.md, ADR-006 | Chroma AWS hosting (**OQ-1**); SSE on API GW (**OQ-3b**)                                   |
| 7   | Core domain model        | IMPLEMENTED | ORM + domain types for user…citation                                    | schema + state-machine tests          | —                       | ADR-019                  | Extra `documents.metadata` (**OQ-14**); no multi-doc conversation join (**OQ-8**)          |
| 8   | Authentication           | IMPLEMENTED | Register/login/refresh/logout; Argon2id; JWT rotation                   | `test_auth*.py`, password/token tests | Refresh family kill     | ADR-007                  | No email verify / reset (**OQ-24**); registration can reveal email existence               |
| 9   | Authorization            | IMPLEMENTED | Owner checks; foreign IDs → not found                                   | `test_authorization*.py`              | IDOR hardening          | ADR-013                  | —                                                                                          |
| 10  | Document upload          | PARTIAL     | Multipart PDF validation (ext/MIME/size/signature)                      | documents + ingestion tests           | Caps + signature        | ADR-020                  | **50 MB vs API Gateway/Lambda** (**OQ-3**); structural PDF validity mainly in worker       |
| 11  | Document storage         | IMPLEMENTED | System object keys; S3 + filesystem adapters                            | S3/storage contracts                  | Keys not user filenames | ADR-014                  | Browser download/presign still **OQ-3**                                                    |
| 12  | Processing pipeline      | IMPLEMENTED | Async stages to READY/FAILED                                            | ingestion/indexing/async tests        | Controlled user errors  | ADR-006/011/015/019      | Commit-then-enqueue (**OQ-27** provisional)                                                |
| 13  | Text extraction          | IMPLEMENTED | PyMuPDF page-aware extraction                                           | extractor + pipeline tests            | Sanitized metadata      | ADR-015                  | OCR not required (§3)                                                                      |
| 14  | Chunking                 | IMPLEMENTED | Configurable semantic-ish chunking                                      | `test_chunking.py`                    | —                       | ADR-003                  | Tokenizer provisional (**OQ-16**)                                                          |
| 15  | Embeddings               | IMPLEMENTED | Port + fake + OpenAI-compatible                                         | embedding provider tests              | Keys not logged         | ADR-004                  | Prod vendor open (**OQ-17**)                                                               |
| 16  | Vector storage           | PARTIAL     | Chroma adapter + tenant filters                                         | chroma + indexing tests               | Owner isolation         | ADR-002                  | **No Chroma host in Terraform** (**OQ-1**)                                                 |
| 17  | Retrieval pipeline       | IMPLEMENTED | Staged retrieval service                                                | retrieval tests                       | Scope checks            | ADR-016                  | —                                                                                          |
| 18  | Hybrid retrieval         | IMPLEMENTED | Dense + PG FTS + RRF                                                    | hybrid + keyword tests                | Owner filters           | ADR-016                  | Language-specific FTS deferred (**OQ-4**)                                                  |
| 19  | Reranking                | PARTIAL     | Port + stage; default `none` fail-open                                  | reranking tests                       | Fail-open               | ADR-016                  | No prod reranker vendor (**OQ-17**)                                                        |
| 20  | Context assembly         | IMPLEMENTED | Limits, dedupe, diversity                                               | retrieval domain tests                | —                       | ADR-016                  | —                                                                                          |
| 21  | Question answering       | IMPLEMENTED | Grounded prompt + short-circuit                                         | answer/prompting tests                | No invention policy     | ADR-017/018              | Model residual risk (**OQ-29**)                                                            |
| 22  | Prompt injection         | IMPLEMENTED | Delimiters, escape, adversarial suite                                   | `test_prompt_injection.py`            | Docs ≠ system           | ADR-017/018              | Model-level residual                                                                       |
| 23  | Citation system          | PARTIAL     | Persisted citations; `[n]` validation                                   | answering + frontend citation tests   | Validated refs only     | ADR-018                  | **Wire DTO omits `filename`** (UI joins documents) — deviation from §23 JSON sketch        |
| 24  | Citation UX              | PARTIAL     | Panel: page, quote, disclaimer, doc link                                | vitest + e2e chat                     | Disclaimer              | ADR-021                  | **No PDF viewer / page navigation** (**OQ-3**)                                             |
| 25  | Conversational RAG       | IMPLEMENTED | History → rewrite + answer                                              | follow-up answering tests             | History as data         | ADR-018                  | —                                                                                          |
| 26  | Query rewriting          | IMPLEMENTED | LLM rewriter; original stored                                           | `test_query_rewriting.py`             | Fail-open               | —                        | —                                                                                          |
| 27  | Multi-document questions | PARTIAL     | Per-ask `document_ids`; collection scope                                | conversation/retrieval tests          | Foreign → not found     | ADR-016                  | No durable multi-doc conversation scope (**OQ-8**)                                         |
| 28  | Conversation persistence | IMPLEMENTED | CRUD + immutable messages                                               | conversation tests                    | Owner-only              | —                        | SYSTEM message policy (**OQ-18**)                                                          |
| 29  | Document management      | PARTIAL     | List/filter/rename/move/delete/reprocess/reindex                        | documents tests                       | Ownership               | ADR-019                  | No semantic `POST /search` (**OQ-10**)                                                     |
| 30  | Collections              | PARTIAL     | CRUD; attach via document PATCH                                         | collections tests                     | Foreign 404             | —                        | No `?delete_documents=true` (**OQ-9**)                                                     |
| 31  | Document deletion        | IMPLEMENTED | Tombstone + purge saga                                                  | lifecycle tests                       | Soft-delete             | ADR-019                  | Tombstone purge job open                                                                   |
| 32  | Re-indexing              | IMPLEMENTED | `/reprocess`, `/reindex`                                                | lifecycle/indexing tests              | —                       | ADR-019                  | —                                                                                          |
| 33  | API design               | IMPLEMENTED | Versioned REST matching §33 (+ stream)                                  | `test_openapi.py`                     | Bearer                  | —                        | Extra stream route (allowed)                                                               |
| 34  | API versioning           | IMPLEMENTED | `/api/v1/...`                                                           | OpenAPI/API tests                     | —                       | —                        | —                                                                                          |
| 35  | API errors               | IMPLEMENTED | Structured `{error:{…,request_id}}`                                     | `test_error_handling.py`              | No stack leaks          | —                        | —                                                                                          |
| 36  | Request correlation      | IMPLEMENTED | Middleware binds request id                                             | `test_request_id.py`                  | —                       | —                        | —                                                                                          |
| 37  | Rate limiting            | IMPLEMENTED | Auth/upload/ask/AI; Redis/memory                                        | rate-limit tests                      | User+IP                 | —                        | —                                                                                          |
| 38  | AI cost controls         | PARTIAL     | Usage ledger + pricing JSON                                             | `test_quotas.py`                      | Quotas                  | OQ-11                    | Answer API exposes tokens more than USD estimate                                           |
| 39  | Usage limits             | PARTIAL     | Server quotas enforced                                                  | quota tests                           | Server-side             | OQ-11                    | **No usage payload on `/users/me`**; no dashboard usage UI                                 |
| 40  | Frontend application     | PARTIAL     | Auth, dashboard, docs, chat+SSE+citations                               | vitest + Playwright                   | sessionStorage refresh  | ADR-021                  | Usage UI; PDF page jump; cookie transport (**OQ-19**)                                      |

---

## §§41–70 UX, data, ops, security, quality

| §   | Title                  | Status          | Implementation                                     | Tests                     | Security           | Docs                    | Gaps / deviations                                                                      |
| --- | ---------------------- | --------------- | -------------------------------------------------- | ------------------------- | ------------------ | ----------------------- | -------------------------------------------------------------------------------------- |
| 41  | Responsive UI          | PARTIAL         | Tailwind breakpoints; mobile nav                   | limited e2e viewport      | —                  | —                       | No device-matrix CI                                                                    |
| 42  | Accessibility          | PARTIAL         | Labels, dialogs, aria-live, focus rings            | component/unit            | —                  | —                       | **No axe/WCAG CI**; AA not claimed                                                     |
| 43  | Loading/failure        | IMPLEMENTED     | Query-state + badges + polling                     | status tests, e2e         | aria-live          | —                       | —                                                                                      |
| 44  | Streaming              | PARTIAL         | SSE API + UI; persist only on success              | answer_stream + sse tests | Mid-stream discard | OQ-3b, OQ-18            | **Deployed API Gateway streaming open**                                                |
| 45  | Database architecture  | IMPLEMENTED     | PG + Alembic migrations; indexes/FTS               | integration repos         | —                  | ADR-012; OQ-23/28       | Lambda pool strategy open                                                              |
| 46  | Transactions           | IMPLEMENTED     | UnitOfWork; deletion saga                          | rollback + lifecycle      | —                  | ADR-019                 | —                                                                                      |
| 47  | Caching                | PARTIAL         | Redis rate-limit (+ optional queue)                | ratelimit/queue tests     | Key scoping        | architecture.md         | **No Redis app cache/locks** (over-claimed historically)                               |
| 48  | Background processing  | IMPLEMENTED     | Jobs + SQS/Redis/memory; TF queue                  | async + worker tests      | DLQ                | ADR-006; OQ-2           | 15m Lambda vs huge PDFs open                                                           |
| 49  | Idempotency            | IMPLEMENTED     | content_hash; job CAS; usage keys                  | upload/lifecycle/quota    | Per-owner dedup    | OQ-6                    | —                                                                                      |
| 50  | Observability          | IMPLEMENTED     | Structured JSON logs + correlation                 | `test_logging.py`         | Redaction          | —                       | Discipline still required                                                              |
| 51  | Metrics                | PARTIAL         | EMF HTTP/docs/RAG/AI                               | unit paths                | Low cardinality    | OQ-21                   | Missing some §51 catalog items (pages/chunks/cost as EMF)                              |
| 52  | Distributed tracing    | PARTIAL         | OTel SDK + spans                                   | `test_tracing.py`         | Safe attrs         | OQ-21                   | **ADOT on Lambda incomplete**                                                          |
| 53  | Security requirements  | PARTIAL         | Authz, validation, headers, SSRF blocks, injection | many suites               | —                  | security.md             | Cookie CSRF path open (**OQ-19**)                                                      |
| 54  | Secrets management     | IMPLEMENTED     | .env.example; SM + hydrate; OIDC                   | secrets hydration tests   | No secrets in Git  | deployment.md           | —                                                                                      |
| 55  | Dependency security    | IMPLEMENTED     | gitleaks, pip-audit, pnpm audit, Trivy             | CI security/containers    | Pinned actions     | ADR-010                 | —                                                                                      |
| 56  | Docker                 | PARTIAL         | Multi-stage, non-root, pinned, scanned             | CI container build        | —                  | OQ-2                    | **Not distroless** (Lambda RIC)                                                        |
| 57  | Infrastructure as Code | PARTIAL         | Full module set + staging/production roots         | TF CI                     | State bootstrap    | ADR-008                 | Chroma module placeholder; **not live-applied** from GH                                |
| 58  | IAM                    | PARTIAL         | API/worker/migrate/deploy roles; OIDC pin          | TF validate               | Boundaries         | ADR-022                 | Some `Resource=*` where AWS requires; monitoring role thin                             |
| 59  | CI pipeline            | IMPLEMENTED     | Full gate matrix in `ci.yml`                       | aggregator                | Fail-closed        | —                       | Tip-of-main can still go red (fix unused TF vars)                                      |
| 60  | CD pipeline            | PARTIAL         | Staging/production workflows + smoke scripts       | scripts exist             | OIDC only          | deployment.md           | **No successful live staging/prod deploy**                                             |
| 61  | Testing strategy       | IMPLEMENTED     | Unit / integration / API / e2e                     | CI + 90% coverage         | Authz adversarial  | —                       | Depth varies by area                                                                   |
| 62  | RAG evaluation         | IMPLEMENTED     | `evals/` + CI FakeLLM                              | eval harness              | —                  | methodology.md, ADR-009 | Real LLM not PR-gated (OQ-22)                                                          |
| 63  | Hallucination tests    | IMPLEMENTED     | Insufficient-evidence / groundedness cases         | eval + answering          | —                  | OQ-29                   | Not free-form live-LLM hallucination suite                                             |
| 64  | Adversarial tests      | PARTIAL         | Corrupt PDF, types, duplicates, injection, IDOR    | multiple suites           | —                  | —                       | No dedicated PDF exploit corpus                                                        |
| 65  | Performance            | NOT_IMPLEMENTED | Async design only                                  | **No p95 harness**        | —                  | —                       | §65 budgets unmet as measured SLOs                                                     |
| 66  | Reliability            | IMPLEMENTED     | Backoff, retries, duplicate handling               | jobs/settings             | —                  | —                       | —                                                                                      |
| 67  | Failure handling       | IMPLEMENTED     | Typed errors; stream discard; retryable jobs       | API/answering/ingestion   | No silent invent   | —                       | Rerank fail-open intentional                                                           |
| 68  | Privacy                | PARTIAL         | Minimize + log redaction                           | logging tests             | —                  | OQ-19                   | sessionStorage refresh elevates XSS blast radius                                       |
| 69  | Data deletion          | PARTIAL         | Document delete saga + UI                          | lifecycle                 | Tombstones         | ADR-019                 | **No account deletion** (**OQ-24**); no tombstone purge; collection cascade (**OQ-9**) |
| 70  | Configuration          | IMPLEMENTED     | Core/API settings + fail-closed prod               | settings tests            | SecretStr          | .env.example            | —                                                                                      |

---

## §§71–91 Structure, docs, process, readiness

| §   | Title                   | Status          | Implementation                                                 | Tests                  | Gaps / deviations                                                    |
| --- | ----------------------- | --------------- | -------------------------------------------------------------- | ---------------------- | -------------------------------------------------------------------- |
| 71  | Repository structure    | IMPLEMENTED     | apps/packages/services/infra/docs/scripts/.github/docker/evals | CI builds              | Core in `packages/core` (ADR-001), not app `src/`                    |
| 72  | Backend boundaries      | IMPLEMENTED     | domain/application/infrastructure split                        | architecture AST tests | —                                                                    |
| 73  | AI provider abstraction | IMPLEMENTED     | LLM/embedding/rerank ports + adapters                          | provider tests         | Vendor open OQ-17                                                    |
| 74  | RAG service boundary    | IMPLEMENTED     | `RagService`                                                   | RAG/answering tests    | —                                                                    |
| 75  | API documentation       | IMPLEMENTED     | FastAPI OpenAPI                                                | `test_openapi.py`      | Deployed docs disabled                                               |
| 76  | README                  | PARTIAL         | Present; status/sections refreshed in this audit               | doc-link CI            | Was stale (“liveness only”); keep honest about non-live AWS          |
| 77  | Architecture docs       | IMPLEMENTED     | architecture + api/database/rag/ops docs; ADRs; methodology    | doc-link CI            | Treat this compliance table + code as authority if any ADR body lags |
| 78  | ADRs                    | PARTIAL         | ADR-001–007, 010–022; **008/009 added**                        | —                      | Still provisional on several OQs                                     |
| 79  | AI-first development    | PARTIAL         | SPEC + OQs + fail-closed CI                                    | CI                     | Doc drift / premature “v1.0.0” without live deploy                   |
| 80  | Agent operating rules   | N/A             | Process expectations                                           | —                      | Not enforceable as code                                              |
| 81  | Incremental development | PARTIAL         | Conventional commits; plan docs                                | CI                     | Large slices; release commits while CD blocked                       |
| 82  | Definition of Done      | PARTIAL         | Strong CI DoD                                                  | CI                     | Docs/security now present; live CD/OQs incomplete                    |
| 83  | Code quality            | IMPLEMENTED     | mypy strict, ruff, ports, architecture tests                   | unit/integration       | Open OQs remain                                                      |
| 84  | Dependency policy       | PARTIAL         | Locks + audits                                                 | security CI            | No automated “justify dep” beyond review; **no branch protection**   |
| 85  | Git requirements        | PARTIAL         | Mostly conventional                                            | —                      | Style variance                                                       |
| 86  | Branch protection       | NOT_IMPLEMENTED | —                                                              | —                      | GitHub `main` unprotected (API)                                      |
| 87  | Environment strategy    | PARTIAL         | Local compose; TF envs; CD YAML                                | Local CI compose       | Staging/prod **not live**; Chroma hosting gap                        |
| 88  | Production readiness    | PARTIAL         | See checklist below                                            | —                      | Not production-ready overall                                         |
| 89  | Portfolio demonstration | PARTIAL         | Strong local/CI demo                                           | e2e + evals            | **No live AWS demo** without operator setup                          |
| 90  | Final product principle | PARTIAL         | Grounding/evals/security design                                | injection + eval       | Undermined by non-live deploy / open OQs                             |
| 91  | Specification authority | IMPLEMENTED     | SPEC + OQs + ADRs process                                      | —                      | Doc drift must be avoided going forward                              |

---

## §88 Production readiness checklist (evidence)

Legend: `[x]` met in-repo with tests · `[~]` partial / designed-not-live · `[ ]` missing

### Application

- [x] Authentication
- [x] Authorization
- [~] Document lifecycle (download / cite-page navigation open — OQ-3)
- [x] Collections
- [x] Conversations
- [x] Multi-document RAG
- [x] Citations
- [x] Query rewriting
- [x] Prompt injection protections
- [x] Quotas
- [x] Rate limiting

### Backend

- [x] Strict typing
- [x] Unit / integration / API tests
- [x] Error handling
- [x] Structured logging
- [~] OpenTelemetry (app yes; ADOT Lambda wiring OQ-21)
- [x] Metrics (EMF partial vs full catalog)

### AI

- [x] Provider abstraction
- [x] Retrieval / groundedness / citation evaluation (`evals/`)
- [~] Hallucination suite (groundedness/refusal; not live free-form LLM)
- [x] Adversarial prompt injection tests
- [x] Token/cost tracking (ledger)

### Infrastructure

- [x] Terraform (code)
- [~] Staging (workflow; **not live green**)
- [ ] Production (no successful CD; GH Environment setup required)
- [~] IAM least privilege (roles exist; wildcards where forced)
- [x] Secrets Manager path
- [x] S3 / PostgreSQL / Redis / API Gateway / Lambda / CloudWatch (as code)
- [~] ChromaDB (app + local/CI; **AWS hosting OQ-1**)

### CI/CD

- [x] CI design (fail-closed gates)
- [x] Security scanning / Docker builds / TF validate
- [~] Staging deployment + smoke (scripts/workflows only)
- [ ] Production deployment

### Documentation

- [~] README (updated; keep current)
- [~] Architecture docs
- [x] API documentation (OpenAPI)
- [~] ADRs (008/009 filled; several provisional)
- [x] Deployment guide
- [x] Security documentation (`docs/security.md`)
- [x] RAG evaluation documentation

---

## Highest-priority incomplete items

1. **Operator AWS + GitHub Environment setup** so staging CD can go green (`deployment.md`).
2. **OQ-1** Chroma hosting for deployed environments.
3. **§86** Enable GitHub branch protection on `main`.
4. **OQ-3 / §24** PDF download and page navigation for citations.
5. **OQ-3b / §44** Production SSE transport through API Gateway / Function URL.
6. **§65** Measured latency SLOs (or consciously amend SPEC).
7. **§39–§40** Usage visibility on `/users/me` and dashboard.
8. **OQ-10** Semantic search endpoint if still required by SPEC.
9. **OQ-19** Cookie refresh for production frontend.
10. **OQ-24** Account deletion / email verification if required for readiness.

Items that **must not** be closed silently (need ADR + SPEC/OQ update): OQ-1, OQ-2, OQ-3, OQ-3b, OQ-8, OQ-9, OQ-10, OQ-17, OQ-19, OQ-21, OQ-22, OQ-24, OQ-25.

---

## Fixes applied during this audit

| Change                                                               | Spec gap addressed             |
| -------------------------------------------------------------------- | ------------------------------ |
| `docs/security.md` created                                           | §76/§88 security documentation |
| ADR-008, ADR-009 written; index updated                              | §78 pending ADRs               |
| README status/stack/eval/API sections corrected                      | §76 honesty                    |
| Production TF vars wired (`nat_gateway_count`, endpoints, flow logs) | CI TFLint unused declarations  |

No open question was silently “resolved” in product behaviour.

---

## How to re-audit

1. Re-read `SPECIFICATIONS.md` section headings.
2. For each §, search code + tests; distrust docs alone.
3. Confirm GitHub: CI tip green, CD staging success, Environment vars, branch protection.
4. Update this file’s date and any status that changed; link new ADRs when OQs close.
