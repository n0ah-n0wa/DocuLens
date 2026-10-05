# ADR-008 — AWS deployment architecture

**Status:** Accepted (provisional)
**Date:** 2026-10-02
**Specification references:** §5.4, §5.5, §56–§60, §87–§88
**Related:** OQ-1, OQ-2, OQ-3, OQ-3b, OQ-19, OQ-21, OQ-23; ADR-006, ADR-020, ADR-022

## Context

The specification requires AWS as the cloud target (Lambda, API Gateway, RDS, ElastiCache, S3,
CloudWatch, IAM, Secrets Manager) with Terraform and GitHub Actions OIDC CD. Several hosting and
transport questions remain open (Chroma placement, Lambda time limits vs large PDFs, upload size vs
API Gateway, SSE through API Gateway).

## Decision

- **IaC:** Separate Terraform roots for `staging` and `production` under
  `infrastructure/terraform/envs/`, composed from reusable modules (networking, compute, database,
  storage, cache, queue, iam, secrets, observability, api-gateway, frontend, ecr, kms, vector-store
  placeholder).
- **Compute (provisional):** API, worker, and migrate as Lambda **container images** with
  `awslambdaric` (OQ-2 provisional). Local remains Docker Compose.
- **AuthN to AWS:** GitHub Actions OIDC only; environment-scoped deploy roles (ADR-022).
- **CD:** Staging after green CI on `main`; production after green staging + GitHub Environment
  approval (`cd-staging.yml`, `cd-production.yml`).
- **Migrations:** Dedicated migrate Lambda invoked synchronously by deploy (OQ-23 provisional).
- **Frontend:** Static Next export on S3 + CloudFront with WAF and security headers (OQ-19 hosting
  mode provisional).
- **Vectors:** Application uses the Chroma adapter (ADR-002); **where Chroma runs in AWS remains
  OQ-1** — Terraform `vector-store` records URL/token wiring only.

## Alternatives considered

| Alternative                       | Why not (for now)                                              |
| --------------------------------- | -------------------------------------------------------------- |
| ECS-first for API/worker          | Spec prefers Lambda; ECS remains upgrade if OQ-1/OQ-2 force it |
| Presigned upload required day-one | ADR-020 keeps direct multipart within platform caps            |
| Skip WAF / logging                | Rejected for production hardening baseline                     |

## Consequences

- Live deploy still requires operator setup (state bootstrap, GitHub Environment variables, Chroma
  URL, AI secrets). Design ≠ demonstrated production until staging CD is green.
- Closing OQ-1/OQ-2/OQ-3b may amend this ADR and §5.4.
