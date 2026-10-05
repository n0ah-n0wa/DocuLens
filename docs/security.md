# Security

Controls that implement SPECIFICATIONS.md §53–§55, §58, and related ADRs. This document describes
**application and IaC controls present in the repository**. Cloud edge controls apply when Terraform
is applied; they are not proven by app unit tests alone.

## Authentication and sessions

- Passwords: Argon2id (`packages/core/src/doculens/infrastructure/security/passwords.py`).
- Access tokens: short-lived JWT (Bearer header); refresh tokens rotate and revoke families
  ([ADR-007](decisions/ADR-007-authentication-strategy.md)). The API does not use cookies.
- Frontend: access token in memory; refresh token in `sessionStorage` pending cookie transport
  (**OQ-19**, [ADR-021](decisions/ADR-021-frontend-foundation.md)).
- Auth, upload, ask, and AI-ops endpoints share configurable rate-limit budgets (Redis when deployed).

## Authorization

- Every document, collection, and conversation mutation is owner-scoped.
- Cross-user identifiers return **not found** (not forbidden) to avoid IDOR signals
  ([ADR-013](decisions/ADR-013-authorization-responses.md)).
- Vector search and object keys are owner-scoped in adapters.

## Upload and document content

- Extension, declared MIME, size, and PDF signature checks before processing
  ([ADR-020](decisions/ADR-020-document-upload-transport.md)).
- Object keys are system-owned (`documents/{user_id}/{document_id}/…`), never raw filenames
  ([ADR-014](decisions/ADR-014-object-storage.md)).
- Parser runs in an isolated worker process with time/memory bounds.
- Quotas and rate limits bound abuse (§37–§39); daily usage is recorded in `usage_events`.

## Prompt injection and grounded answers

- Documents and history are delimited untrusted data; system instructions are separate
  ([ADR-017](decisions/ADR-017-grounded-prompt.md), [ADR-018](decisions/ADR-018-answering-pipeline.md)).
- Citations must resolve to retrieved context indexes; invented references are dropped.
- Adversarial coverage: `packages/core/tests/unit/test_prompt_injection.py` and eval categories.
- Frontend renders answers and citation quotes as text, never as HTML.

## Secrets and configuration

- No application secrets in Git. Local: git-ignored `.env`. Deployed: AWS Secrets Manager + OIDC
  deploy roles ([ADR-022](decisions/ADR-022-github-actions-oidc.md), [deployment.md](deployment.md)).
- Startup validation refuses unsafe deployed settings (fake AI providers, console-only logs,
  non-SQS queues, cleartext Redis, unencrypted S3, etc.).

## Network and cloud surface (IaC — when applied)

- Private subnets for data plane; TLS required to RDS/Redis; S3 public access blocked; CMK encryption.
- CloudFront + regional API Gateway WAFv2 rule sets; CSP/security headers on the static frontend via
  CloudFront (Next.js runtime headers are skipped for `DOCULENS_STATIC_EXPORT=1`).
- GitHub Actions assumes environment-scoped IAM roles only (no long-lived AWS keys in GitHub).

## Dependency and supply-chain scanning

CI runs gitleaks, `pip-audit`, `pnpm audit`, container Trivy, and Terraform Trivy config
(`.github/workflows/ci.yml`).

## Known residual risks

| Risk                                         | Status                                                       |
| -------------------------------------------- | ------------------------------------------------------------ |
| Chroma hosting in AWS (OQ-1)                 | Open — vectors work locally/CI; production hosting undecided |
| API Gateway body size vs 50 MB upload (OQ-3) | Deployed cap ≤ 10 MB (ADR-020); presigned upload not built   |
| SSE through API Gateway (OQ-3b)              | Open for deployed streaming; sync ask works                  |
| Cookie refresh / CSRF (OQ-19)                | Provisional sessionStorage                                   |
| Account deletion / email verify (OQ-24)      | Not implemented                                              |
| Registration email enumeration               | 409 on known email; rate-limited (awaits OQ-24 verification) |
| Live staging/production CD                   | Workflows exist; operator AWS/GitHub setup still required    |

Unresolved ambiguities stay in [planning/open-questions.md](planning/open-questions.md) and must not be
closed silently (§91). Operator troubleshooting: [operations.md](operations.md).
