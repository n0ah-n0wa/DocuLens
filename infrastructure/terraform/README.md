# Terraform

Infrastructure as Code for the `staging` and `production` environments (SPECIFICATIONS.md §57).
`local` is not a Terraform environment: it is the docker compose stack in `docker/compose.yaml`
(OQ-26).

**Honest status:** modules and CD scripts are complete in this repository. A successful live apply
still depends on operator bootstrap (state bucket, OIDC, GitHub Environments, real `chroma_url`).
See [`docs/deployment.md`](../../docs/deployment.md) and [`docs/operations.md`](../../docs/operations.md).

## Layout

```text
infrastructure/terraform/
├── bootstrap/state/   # one-time S3 + DynamoDB lock (+ optional KMS); apply manually, not via CD
├── envs/
│   ├── staging/       # root module for staging
│   └── production/    # root module for production
├── modules/
│   ├── networking/    # VPC, subnets, NAT, security groups, VPC endpoints
│   ├── kms/           # customer-managed encryption key
│   ├── ecr/           # API and worker container registries
│   ├── storage/       # documents S3 bucket
│   ├── database/      # RDS PostgreSQL
│   ├── cache/         # ElastiCache Redis
│   ├── queue/          # SQS + DLQ (document processing)
│   ├── secrets/       # Secrets Manager (generated JWT/DB/Redis; no hardcoded secrets)
│   ├── frontend/      # S3 + CloudFront static web (OQ-19 provisional)
│   ├── iam/           # least-privilege Lambda + GitHub OIDC deploy role
│   ├── compute/       # API / worker / migrate Lambdas
│   ├── api-gateway/   # HTTP API in front of the API Lambda
│   ├── observability/ # CloudWatch log groups and alarms
│   └── vector-store/  # OQ-1 placeholder (Chroma hosting deferred)
└── README.md
```

## Remote state

Each environment uses a partial S3 backend (`backend.tf`). Bucket name, region and lock table are
supplied at init time from a git-ignored `backend.hcl`:

```bash
cd infrastructure/terraform/envs/staging
cp backend.hcl.example backend.hcl   # edit bucket / region / dynamodb_table
terraform init -backend-config=backend.hcl
```

State keys:

| Environment | State key                               |
| ----------- | --------------------------------------- |
| staging     | `doculens/staging/terraform.tfstate`    |
| production  | `doculens/production/terraform.tfstate` |

Bootstrap the state bucket and DynamoDB lock table once via `bootstrap/state/` before the first
environment apply. The GitHub Actions OIDC provider is created by Terraform when
`create_github_oidc_provider = true` (see [`docs/deployment.md`](../../docs/deployment.md)).

## Working with an environment

```bash
cd infrastructure/terraform/envs/staging
cp terraform.tfvars.example terraform.tfvars   # edit; never commit secrets
terraform init -backend-config=backend.hcl
terraform fmt -recursive ../..
terraform validate
terraform plan -var-file=terraform.tfvars
# Prefer CD for apply (OIDC deploy role). Local apply only with equivalent credentials.
```

CI runs `terraform fmt -check -recursive` and, per environment,
`terraform init -backend=false && terraform validate`, plus `tflint` and a Trivy configuration
scan of `infrastructure/terraform` (misconfigurations fail the job). CD workflows apply after
plan via [`.github/workflows/cd-staging.yml`](../../.github/workflows/cd-staging.yml) /
[`cd-production.yml`](../../.github/workflows/cd-production.yml).

## Notes

- **Lambda images:** CD pushes digest-tagged images to ECR, then sets `api_image_uri` /
  `worker_image_uri` on apply.
- **Secrets:** JWT, DB URL, and Redis URL live only in Secrets Manager. Lambdas receive
  `APP_SECRETS_ARN` and hydrate at cold start. LLM/embedding keys via
  `TF_VAR_EXTRA_SECRET_VALUES` (GitHub Environment secret) or the AWS Console.
- **ChromaDB (OQ-1):** `vector-store` does not provision a server; set `chroma_url` to a real
  `https://` endpoint (placeholders fail CD).
- **GitHub OIDC (§5.5):** one account-level IdP plus a dedicated deploy role per environment.
  Trust is `repo:<org>/<repo>:environment:<staging|production>` only. Validate with
  `uv run python scripts/validate_github_oidc.py`.
- **Cost knobs:** staging disables interface VPC endpoints by default; production defaults to
  `az_count = 3` and `nat_gateway_count = 3` (raised to `max(nat, az)`), and caps worker SQS
  concurrency.
