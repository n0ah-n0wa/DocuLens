# Deployment

How DocuLens reaches AWS (SPECIFICATIONS.md §5.5, §56–§60). Local development stays on
`docker/compose.yaml` and does not use these roles.

**Honest status:** CD workflows, Terraform, smoke scripts, and rollback paths are implemented in
this repository. A green live staging/production deploy still requires operator bootstrap (state,
OIDC, GitHub Environments, real `CHROMA_URL`, LLM keys). Troubleshooting:
[`operations.md`](operations.md).

## Staging pipeline

```text
PR / merge to main
    → CI (all gates must be green)
    → CD · staging
         1. CI gate
         2. Build (API + worker images; artifacts)
         3. Security scan (Trivy on images)
         4. Infrastructure plan (terraform plan via OIDC)
         5. Deploy staging
              • push images to ECR (digest tags)
              • terraform apply (image digests + infra; Secrets Manager unchanged in Git)
              • static frontend → S3 + CloudFront invalidation
              • invoke migrate Lambda
         6. Smoke
              • functional API smoke (scripts/cd/smoke_staging.py): frontend + API availability,
                auth, upload, processing, indexing, RAG, citations, deletion — creates and
                deletes its own fixture data
              • Playwright UI smoke (landing + login)
```

Workflow: [`.github/workflows/cd-staging.yml`](../.github/workflows/cd-staging.yml).  
Scripts (reproducible locally with the same OIDC role): [`scripts/cd/`](../scripts/cd/).

## Production pipeline

Production deploys only after **CI passes**, **staging deployment succeeds**, and **staging
smoke tests pass** (the staging workflow is green for that commit). Human approval is enforced
by the GitHub Environment `production` (required reviewers). Deploys never use a developer's
laptop as the source of truth (§60).

```text
CD · staging success on main (same git SHA)
    → CD · production
         1. Gates: CI success + staging CD success for the SHA
         2. Production protection (GitHub Environment approval)
         3. Build (API + worker images from that SHA)
         4. Security scan (Trivy on images)
         5. Infrastructure plan (terraform plan via OIDC)
         6. Deploy production
              • snapshot previous Lambda digests → production-pre-deploy.env (rollback)
              • push images to production ECR (digest tags)
              • terraform apply
              • static frontend → S3 + CloudFront invalidation
              • invoke migrate Lambda
         7. Smoke (same functional + Playwright suite as staging)
```

Workflow: [`.github/workflows/cd-production.yml`](../.github/workflows/cd-production.yml).

Triggers:

| Trigger             | Behaviour                                                                |
| ------------------- | ------------------------------------------------------------------------ |
| `workflow_run`      | After **CD · staging** completes on `main`; gates refuse non-success     |
| `workflow_dispatch` | Optional SHA; still requires green CI + green staging CD for that commit |

Scripts: `plan_production.sh`, `deploy_production.sh`, `smoke_production.sh`,
`rollback_production.sh`.

## Authentication model (OIDC only)

CD never stores AWS access keys in GitHub. Workflows request a short-lived OIDC token from GitHub,
then call `sts:AssumeRoleWithWebIdentity` against an environment-specific IAM deploy role.

```text
GitHub Actions job (environment: staging|production)
        │  id-token: write
        ▼
token.actions.githubusercontent.com  (JWT aud=sts.amazonaws.com,
                                      sub=repo:ORG/REPO:environment:ENV)
        │
        ▼
IAM OIDC provider (one per AWS account)
        │
        ▼
IAM deploy role  doculens-<env>-deploy-*
  • trust: that repo + that environment only
  • permissions: ECR, Lambda, Terraform apply surface, frontend S3/CloudFront
```

Decision record: [ADR-022](decisions/ADR-022-github-actions-oidc.md).

## Secrets (never through Git)

| Secret                       | Where it lives                                                                |
| ---------------------------- | ----------------------------------------------------------------------------- |
| `JWT_SECRET`                 | Secrets Manager (Terraform `random_password`, module `secrets`)               |
| `DATABASE_URL` / `REDIS_URL` | Secrets Manager (composed from RDS/ElastiCache; not committed)                |
| LLM / embedding API keys     | Secrets Manager extras — set only via GitHub Environment **secret**           |
|                              | `TF_VAR_EXTRA_SECRET_VALUES` (JSON object) at plan/apply time, or AWS Console |
| AWS access keys              | **Forbidden** in GitHub                                                       |

Lambdas receive `APP_SECRETS_ARN` only. At cold start,
[`hydrate_secrets_into_environ`](../packages/core/src/doculens/infrastructure/secrets.py)
loads the JSON secret into the process environment before settings validation. Optional
`CHROMA_API_TOKEN_SECRET_ARN` is hydrated into `CHROMA_API_TOKEN`. CD never echoes secret
values into logs or artifacts. Put LLM/embedding API keys in the Environment secret
`TF_VAR_EXTRA_SECRET_VALUES` (JSON object), never in Git.

## Terraform

| Setting                       | Staging                             | Production                      |
| ----------------------------- | ----------------------------------- | ------------------------------- |
| `create_github_oidc_provider` | `false` (reuse account IdP)         | `true` (create once)            |
| `create_github_deploy_role`   | `true`                              | `true`                          |
| `github_repository`           | `org/repo`                          | same                            |
| Default OIDC `sub`            | `repo:…:environment:staging`        | `repo:…:environment:production` |
| Frontend                      | S3 + CloudFront (`module.frontend`) | same                            |

Outputs used by CD:

```bash
cd infrastructure/terraform/envs/production   # or staging
terraform output -raw deploy_role_arn
terraform output -raw api_gateway_endpoint
terraform output -raw frontend_url
terraform output -raw frontend_bucket_id
terraform output -raw frontend_distribution_id
```

Bootstrap order when staging and production share an account:

1. Apply **production** with `create_github_oidc_provider = true` (creates the IdP + production role).
2. Apply **staging** with `create_github_oidc_provider = false` (looks up the IdP, creates staging role).
3. Configure GitHub Environment variables below, then run **CD · staging**, then **CD · production**.

## Required GitHub configuration

### 1. Environments

Create Environments named exactly `staging` and `production` (match OIDC `sub`).

**Production** must require reviewers (protected deployment / manual approval). Staging may deploy
continuously after green CI on `main`.

Recommended production Environment settings:

- Required reviewers (one or more people/teams)
- Deployment branches: `main` only
- No bypass of protection rules for administrators unless operationally necessary

### 2. Staging Environment variables

| Name                  | Value                                                                           |
| --------------------- | ------------------------------------------------------------------------------- |
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw deploy_role_arn` (staging root)                          |
| `AWS_REGION`          | e.g. `eu-central-1`                                                             |
| `TF_STATE_BUCKET`     | Terraform state bucket name                                                     |
| `TF_LOCK_TABLE`       | DynamoDB lock table name                                                        |
| `TF_STATE_KMS_KEY_ID` | Optional state CMK ARN (`bootstrap/state` output `kms_key_arn`)                 |
| `CHROMA_URL`          | Required `https://` Chroma endpoint (OQ-1; placeholder values fail CD)          |
| `CORS_ALLOW_ORIGINS`  | Comma-separated browser origins (CloudFront URL); wires API GW + `CORS_ORIGINS` |

### 3. Production Environment variables

Same names as staging, but values from the **production** Terraform root (`deploy_role_arn`,
state key `doculens/production/terraform.tfstate`, production `CHROMA_URL`).

### 4. Environment secrets (optional, per env)

| Name                         | Value                                                                  |
| ---------------------------- | ---------------------------------------------------------------------- |
| `TF_VAR_EXTRA_SECRET_VALUES` | JSON object merged into the app Secrets Manager secret (e.g. LLM keys) |

Do **not** create `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

### 5. Workflow permissions

CD jobs set `permissions.id-token: write` and `contents: read`. Gate jobs also need
`actions: read` to query CI / staging run conclusions.

## Rollback process

Prefer rolling forward when safe. When production must be reverted, use one of the paths below.

### A. Full application rollback (preferred)

Redeploy the last known-good git SHA through CD (same gates: that SHA must still have green CI
and green staging CD):

1. Confirm the previous SHA in GitHub Actions (last successful **CD · production** run).
2. Ensure that SHA still has successful **CI** and **CD · staging** runs.
3. Run **CD · production** → `workflow_dispatch` with `git_sha` set to that commit.
4. Approve the `production` Environment when prompted.
5. Wait for deploy + smoke to pass.

This rebuilds images from that commit, applies Terraform with those digests, syncs the matching
frontend, and runs migrations (`alembic upgrade head` — no-op if schema already at head).

### B. Fast Lambda image rollback

Each production deploy writes `.local/production-pre-deploy.env` (uploaded as artifact
`production-rollback-<image_tag>`) with the previous API/worker image URIs.

```bash
# With production deploy-role credentials / OIDC equivalent:
export AWS_REGION=eu-central-1
export TF_STATE_BUCKET=…
export TF_LOCK_TABLE=…
# Download the artifact from the deploy that went bad, or copy production-pre-deploy.env
export ROLLBACK_ENV_FILE=.local/production-pre-deploy.env
./scripts/cd/rollback_production.sh
```

This re-applies the previous container digests via Terraform. Frontend is left unchanged unless
you pass `FRONTEND_ROLLBACK_DIR` pointing at a previous `apps/web/out` tree.

### C. Database / migrations

Migrations are **forward-only** in CD (`alembic upgrade head`). Rolling back Lambda code does
**not** reverse schema.

- If the bad release only changed application code: path A or B is enough.
- If the bad release included a migration that must be undone: write and review an explicit
  forward fix migration (preferred), or run a controlled `alembic downgrade` from an operator
  session with break-glass DB access — never from an unattended CD job.
- Take / verify RDS snapshots before risky production schema changes.

### D. After rollback

1. Run `./scripts/cd/smoke_production.sh` (or wait for CD smoke on a SHA redeploy).
2. Open an incident note: bad SHA, good SHA, path used (A/B/C), migration impact.
3. Block further production deploys until the fix is staged and smoked.

## Local reproduction

### Staging

With AWS credentials equivalent to the staging deploy role (or after `aws sso login` into that role):

```bash
export AWS_REGION=eu-central-1
export TF_STATE_BUCKET=…
export TF_LOCK_TABLE=…
export IMAGE_TAG=sha-$(git rev-parse HEAD)
export ECR_REGISTRY=123456789012.dkr.ecr.$AWS_REGION.amazonaws.com

cat > infrastructure/terraform/envs/staging/terraform.tfvars <<EOF
aws_region                  = "${AWS_REGION}"
chroma_url                  = "${CHROMA_URL:?set a real https:// Chroma URL}"
create_github_oidc_provider = false
create_github_deploy_role   = true
github_repository           = "ORG/DocuLens"
enable_interface_endpoints  = false
enable_vpc_flow_logs        = true
# Optional after first CloudFront URL is known:
# cors_allow_origins        = ["https://d111111abcdef8.cloudfront.net"]
EOF

./scripts/cd/plan_staging.sh
SKIP_IMAGE_BUILD=0 ./scripts/cd/deploy_staging.sh
./scripts/cd/smoke_staging.sh
```

### Production

Same pattern against the production root and role (prefer CD for real releases):

```bash
cat > infrastructure/terraform/envs/production/terraform.tfvars <<EOF
aws_region                  = "${AWS_REGION}"
chroma_url                  = "${CHROMA_URL:?set a real https:// Chroma URL}"
create_github_oidc_provider = true
create_github_deploy_role   = true
github_repository           = "ORG/DocuLens"
enable_interface_endpoints  = true
enable_vpc_flow_logs        = true
EOF

./scripts/cd/plan_production.sh
./scripts/cd/deploy_production.sh
./scripts/cd/smoke_production.sh
```

Smoke covers frontend and API availability, register/login, upload, processing through
`READY`, indexing (`chunk_count` / `indexed_at`), a grounded ask with citations, and
deletion. It never assumes pre-seeded environment data.

## Validating trust and permissions

```bash
uv run python scripts/validate_github_oidc.py
```

After apply, confirm trust and that CD assumed the deploy role (`aws sts get-caller-identity` in the
workflow log shows `…assumed-role/…deploy…`).

## Remaining readiness gaps

| Item                                         | Status                                                                   |
| -------------------------------------------- | ------------------------------------------------------------------------ |
| Secrets hydration (`APP_SECRETS_ARN`)        | Fixed                                                                    |
| Lambda RIC + Mangum / SQS / migrate handlers | Fixed (provisional OQ-2 / OQ-23)                                         |
| Deploy IAM scoped for secrets + role prefix  | Fixed (shared-account residual on `ec2:*`/`rds:*` etc.)                  |
| CloudFront security headers + TLS deny       | Fixed                                                                    |
| Binary tfplan artifact with secrets          | Fixed (text plan only)                                                   |
| `workflow_dispatch` CI gate                  | Fixed (queries CI runs for the SHA)                                      |
| Migrate invoke `FunctionError` check         | Fixed                                                                    |
| Production CD (plan/deploy/smoke/rollback)   | Implemented (gates + Environment approval)                               |
| Chroma hosting (OQ-1)                        | **Open** — smoke RAG needs a real `CHROMA_URL` + token                   |
| LLM/embedding API keys                       | Operator: set `TF_VAR_EXTRA_SECRET_VALUES`                               |
| Alarm SNS                                    | Topic created when `create_alarm_topic=true` (prod); subscribe operators |
| Apply-from-plan (exact plan binary)          | Deferred (secrets vs artifact tradeoff)                                  |

## Production hardening

Production Terraform now enforces stronger defaults than staging: 3 NAT gateways,
interface VPC endpoints and VPC flow logs (forced on), RDS Performance Insights,
`prevent_destroy` guards on RDS/ElastiCache, expanded CloudFront + regional API
WAFv2 rule sets with logging, S3 access logging, SQS queue policies scoped to API/worker
roles, migrate Lambda X-Ray tracing, asyncpg `ssl=require`, and an SNS alarm topic.

Production CORS no longer reads `module.frontend` (avoids a cycle). After the first
CloudFront distribute, set `cors_allow_origins = ["https://<distribution>.cloudfront.net"]`
(or `terraform output -raw frontend_url`) in production tfvars / CD vars.

### Do not deploy production until operators review

**Do not deploy production until operators review** the checklist below:

- [ ] GitHub Environment `production` has **required reviewers** enabled
- [ ] Deployment branches restricted to **`main` only**
- [ ] Bootstrap state stack applied once (`infrastructure/terraform/bootstrap/state`) and
      `TF_STATE_BUCKET` / `TF_LOCK_TABLE` / **`TF_STATE_KMS_KEY_ID`** set on the Environment
- [ ] SNS alarm topic subscribed (`terraform output -raw alarm_topic_arn`) — email/PagerDuty/etc.
- [ ] `cors_allow_origins` set to the CloudFront URL after the first frontend deploy
- [ ] Optional `aws_account_id` set so providers refuse the wrong account
- [ ] Chroma URL/token and `TF_VAR_EXTRA_SECRET_VALUES` (LLM keys) configured
- [ ] Staging CD + smoke green for the same git SHA

## What CI does not do

`.github/workflows/ci.yml` stays AWS-keyless and does not assume the deploy role. It builds and
scans images but does not push to ECR or change staging/production. Staging CD runs only after CI
succeeds on `main`. Production CD runs only after staging CD succeeds for the same commit and a
human approves the `production` Environment (or via `workflow_dispatch` when that commit already
clears the same gates).
