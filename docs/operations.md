# Operations and troubleshooting

Runbook for local development, CI/CD, and (when AWS bootstrap is complete) staging/production.
**Honest status:** application + Terraform + CD workflows are in-repo; a green live AWS deploy still
requires operator GitHub Environment / OIDC / Chroma / secrets setup. See [`deployment.md`](deployment.md)
and the workspace operator notes in [`aws.txt`](../aws.txt) if present.

## Quick orientation

| Concern            | Document / path                                 |
| ------------------ | ----------------------------------------------- |
| Local setup        | [`development.md`](development.md)              |
| Deploy / OIDC / CD | [`deployment.md`](deployment.md)                |
| Security controls  | [`security.md`](security.md)                    |
| Architecture       | [`architecture.md`](architecture.md)            |
| CD scripts         | `scripts/cd/`                                   |
| OIDC validation    | `uv run python scripts/validate_github_oidc.py` |
| Doc link CI        | `uv run python scripts/check_doc_links.py`      |

## Local stack

```bash
make bootstrap          # uv + pnpm + Playwright chromium
cp .env.example .env
make infra-up           # postgres, redis, chroma, minio (loopback only)
make db-upgrade
uv run uvicorn doculens_api.main:create_app --factory --reload --port 8000
uv run python -m doculens_worker          # consumes QUEUE_BACKEND (memory/redis/sqs)
pnpm --filter @doculens/web dev           # :3000
```

Health: `GET /health/live`, `GET /health/ready` (503 lists failing probes).

### Common local failures

| Symptom                                  | Likely cause / fix                                                                   |
| ---------------------------------------- | ------------------------------------------------------------------------------------ |
| `pnpm install` refuses engines           | Node 24 + pnpm 11 required; `corepack enable`                                        |
| Supply-chain / minimumReleaseAge         | Pinned package newer than 24h — wait or pick prior version                           |
| Playwright browser missing               | `pnpm --filter @doculens/web exec playwright install chromium`                       |
| mypy duplicate module                    | Two test files share a basename — rename one                                         |
| Integration tests wiped data             | Never point `DOCULENS_TEST_DATABASE_URL` at a DB you care about                      |
| Port 5432 busy                           | Set `POSTGRES_PORT` and matching `DATABASE_URL` in `.env`                            |
| `docker info` fails (Windows)            | Start Docker Desktop before `make infra-up` / `make docker-build`                    |
| Ready probe fails on Chroma              | Compose Chroma up; check `CHROMA_URL` / `VECTOR_STORE`                               |
| Uploads stuck in `UPLOADED`              | Worker not running; or queue backend mismatch; reconcile runs on Lambda batches only |
| Ask returns insufficient evidence always | Document not `READY`; scope empty; fake embeddings mismatch corpus                   |

## Job queue backends

| `QUEUE_BACKEND` | Where used                      | Notes                                            |
| --------------- | ------------------------------- | ------------------------------------------------ |
| `memory`        | Unit tests / single process     | In-process only — **not** for API + worker split |
| `redis`         | Local compose, critical e2e     | Preferred two-process local development          |
| `sqs`           | Staging / production (required) | Partial batch failure + DLQ; Lambda reconciles   |

## Document processing

| Symptom                   | What to check                                                               |
| ------------------------- | --------------------------------------------------------------------------- |
| Stuck `UPLOADED`          | Worker logs; queue visibility; ADR-011 straggler reconcile (Lambda only)    |
| `FAILED` after validation | Encrypted/corrupt PDF, page limit, empty text policy                        |
| Retries then DLQ          | Embedding/LLM outages leave jobs retryable; exhaustion marks `FAILED` + ACK |
| Duplicate content 409     | Partial unique `(owner_id, content_hash)` for non-deleted docs              |

Manual single-document drive (local):

```bash
uv run python -m doculens_worker process <document-id>
```

## Auth and sessions

| Symptom                      | Notes                                                                  |
| ---------------------------- | ---------------------------------------------------------------------- |
| 401 on every request         | Missing/expired access token; web refreshes once then clears session   |
| Refresh revokes whole family | Concurrent refresh or reuse of rotated token (by design, ADR-007)      |
| 429 on login                 | Auth rate limit (IP + email); wait `Retry-After`                       |
| CORS failures in browser     | Set `CORS_ORIGINS` to exact frontend origin; deployed default is empty |

Production cookie refresh / CSRF remains **OQ-19**; current web uses in-memory access +
`sessionStorage` refresh.

## RAG / ask path

| Symptom                              | Notes                                                             |
| ------------------------------------ | ----------------------------------------------------------------- |
| 503 on ask                           | Provider timeout/failure; nothing persisted — safe to retry       |
| Citations missing                    | Model omitted `[n]` or indexes invalid (dropped server-side)      |
| Stream ends without persisted answer | Client cancel or generation failure — no partial persist          |
| Deployed stream broken               | **OQ-3b**: SSE through API Gateway may not work; sync ask remains |

## Observability

| Signal  | Implementation                                                                                |
| ------- | --------------------------------------------------------------------------------------------- |
| Logs    | structlog; console local / JSON deployed; redaction; correlation ids                          |
| Metrics | CloudWatch EMF namespaces `DocuLens/API`, `/Documents`, `/RAG`, `/AI`                         |
| Traces  | OTEL `none` \| `console` \| `otlp`; ADOT Lambda layer **not** wired in Terraform (**OQ-21**)  |
| Alarms  | Terraform observability module: Lambda errors/throttles, SQS DLQ / age; prod SNS when enabled |

Never expect prompts, answers, document text, or secrets in span attributes or logs.

### Example shapes (local / deployed)

Structured access log fields (illustrative):

```json
{
  "timestamp": "2026-10-03T00:00:00.000Z",
  "level": "info",
  "service": "doculens-api",
  "message": "request completed",
  "request_id": "6f2c…",
  "user_id": "a1b2…",
  "method": "POST",
  "path": "/api/v1/conversations/{id}/messages",
  "status_code": 200,
  "duration_ms": 842
}
```

EMF metrics are emitted as JSON log lines with a `_aws.CloudWatchMetrics` envelope under the
`DocuLens/*` namespaces (counts + latency). Without a live CloudWatch stack, inspect JSON logs
locally with `LOG_FORMAT=json`.

## CI failures

| Job / gate                    | Typical fix                                                                                                |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Backend format/lint/types     | `uv run ruff format . && uv run ruff check --fix . && uv run mypy`                                         |
| Coverage floor                | Add tests; do not lower the gate                                                                           |
| RAG eval                      | Read [`eval/methodology.md`](eval/methodology.md); fix product or case — do not weaken thresholds casually |
| Frontend Prettier             | Ensure `.prettierignore` excludes `.terraform.d` / `.cache`                                                |
| Terraform fmt/validate/tflint | `make` / CI matrix per env; no AWS keys in CI                                                              |
| Trivy / gitleaks / audits     | Fix findings; do not suppress without documented exception                                                 |
| Critical Playwright           | Needs compose + migrate + API + worker; use `scripts/write_critical_e2e_env.py`                            |

## Staging / production CD

Workflows: `.github/workflows/cd-staging.yml`, `cd-production.yml`.  
Full procedure, secrets, rollback A/B/C/D: [`deployment.md`](deployment.md).

### Gates that block a first deploy

1. GitHub Environments `staging` / `production` with `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`,
   `TF_STATE_BUCKET`, `TF_LOCK_TABLE`, real HTTPS `CHROMA_URL` (no placeholder).
2. Terraform state bootstrap (`infrastructure/terraform/bootstrap/state`).
3. OIDC deploy roles applied; `uv run python scripts/validate_github_oidc.py`.
4. `TF_VAR_EXTRA_SECRET_VALUES` for LLM/embedding keys (optional secret JSON).
5. After first CloudFront URL: set `CORS_ALLOW_ORIGINS` / `cors_allow_origins`.

### Deploy smoke failures

Functional smoke (`scripts/cd/smoke_staging.py`) registers, uploads, waits for `READY`, asks with
citations, and deletes fixtures. Failures usually mean: Chroma unreachable, AI keys missing,
migrate Lambda `FunctionError`, CORS, or worker not consuming SQS.

Rollback: prefer redeploy of last green SHA; fast Lambda digest rollback via
`scripts/cd/rollback_production.sh`. Migrations are forward-only in CD.

## What operators should not assume

- That Terraform apply has already succeeded in any AWS account from this repository alone.
- That Chroma is provisioned by Terraform (**OQ-1** — external `CHROMA_URL` required).
- That ADOT/OTLP is on by default in Lambda (**OQ-21**).
- That offline RAG eval equals production model quality.
