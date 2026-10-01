# Deployment

How DocuLens reaches AWS (SPECIFICATIONS.md §5.5, §56–§60). Local development stays on
`docker/compose.yaml` and does not use these roles.

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

Production remains manual approval via [`.github/workflows/cd-production.yml`](../.github/workflows/cd-production.yml).

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
values into logs or artifacts. Put LLM/embedding API keys in the staging Environment secret
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
cd infrastructure/terraform/envs/staging
terraform output -raw deploy_role_arn
terraform output -raw api_gateway_endpoint
terraform output -raw frontend_url
terraform output -raw frontend_bucket_id
terraform output -raw frontend_distribution_id
```

Bootstrap order when staging and production share an account:

1. Apply **production** with `create_github_oidc_provider = true` (creates the IdP + production role).
2. Apply **staging** with `create_github_oidc_provider = false` (looks up the IdP, creates staging role).
3. Configure GitHub Environment variables below, then run **CD · staging**.

## Required GitHub configuration

### 1. Environments

Create Environments named exactly `staging` and `production` (match OIDC `sub`).

**Production** should require reviewers. Staging may deploy continuously after green CI on `main`.

### 2. Staging Environment variables

| Name                  | Value                                                          |
| --------------------- | -------------------------------------------------------------- |
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw deploy_role_arn`                        |
| `AWS_REGION`          | e.g. `eu-central-1`                                            |
| `TF_STATE_BUCKET`     | Terraform state bucket name                                    |
| `TF_LOCK_TABLE`       | DynamoDB lock table name                                       |
| `CHROMA_URL`          | Reachable Chroma endpoint (OQ-1; readiness may fail until set) |

### 3. Staging Environment secrets (optional)

| Name                         | Value                                                                  |
| ---------------------------- | ---------------------------------------------------------------------- |
| `TF_VAR_EXTRA_SECRET_VALUES` | JSON object merged into the app Secrets Manager secret (e.g. LLM keys) |

Do **not** create `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

### 4. Workflow permissions

CD jobs set `permissions.id-token: write` and `contents: read`.

## Local reproduction

With AWS credentials equivalent to the staging deploy role (or after `aws sso login` into that role):

```bash
export AWS_REGION=eu-central-1
export TF_STATE_BUCKET=…
export TF_LOCK_TABLE=…
export IMAGE_TAG=sha-$(git rev-parse HEAD)
export ECR_REGISTRY=123456789012.dkr.ecr.$AWS_REGION.amazonaws.com

# Non-secret tfvars (same keys CD writes; never commit real secrets)
cat > infrastructure/terraform/envs/staging/terraform.tfvars <<EOF
aws_region                  = "${AWS_REGION}"
chroma_url                  = "${CHROMA_URL:-https://chroma.example.internal:8000}"
create_github_oidc_provider = false
create_github_deploy_role   = true
github_repository           = "ORG/DocuLens"
enable_interface_endpoints  = false
enable_vpc_flow_logs        = true
EOF

./scripts/cd/plan_staging.sh
# Builds images if missing; set SKIP_IMAGE_BUILD=1 when reusing scanned local tags
./scripts/cd/deploy_staging.sh
# Functional smoke: creates unique user + handbook PDF, exercises RAG, deletes resources
./scripts/cd/smoke_staging.sh
```

Smoke covers frontend and API availability, register/login, upload, processing through
`READY`, indexing (`chunk_count` / `indexed_at`), a grounded ask with citations, and
deletion. It never assumes pre-seeded staging data.

## Validating trust and permissions

```bash
uv run python scripts/validate_github_oidc.py
```

After apply, confirm trust and that CD assumed the deploy role (`aws sts get-caller-identity` in the
workflow log shows `…assumed-role/…deploy…`).

## Remaining staging readiness gaps

| Item                                         | Status                                                  |
| -------------------------------------------- | ------------------------------------------------------- |
| Secrets hydration (`APP_SECRETS_ARN`)        | Fixed                                                   |
| Lambda RIC + Mangum / SQS / migrate handlers | Fixed (provisional OQ-2 / OQ-23)                        |
| Deploy IAM scoped for secrets + role prefix  | Fixed (shared-account residual on `ec2:*`/`rds:*` etc.) |
| CloudFront security headers + TLS deny       | Fixed                                                   |
| Binary tfplan artifact with secrets          | Fixed (text plan only)                                  |
| `workflow_dispatch` CI gate                  | Fixed (queries CI runs for the SHA)                     |
| Migrate invoke `FunctionError` check         | Fixed                                                   |
| Chroma hosting (OQ-1)                        | **Open** — smoke RAG needs a real `CHROMA_URL` + token  |
| LLM/embedding API keys                       | Operator: set `TF_VAR_EXTRA_SECRET_VALUES`              |
| ADOT / alarm SNS                             | Still deferred (OQ-21 / empty `alarm_actions`)          |
| Apply-from-plan (exact plan binary)          | Deferred (secrets vs artifact tradeoff)                 |

## What CI does not do

`.github/workflows/ci.yml` stays AWS-keyless and does not assume the deploy role. It builds and
scans images but does not push to ECR or change staging. CD runs only after CI succeeds on `main`
(or via manual `workflow_dispatch` when that commit already has green CI).
