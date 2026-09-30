# Terraform

Infrastructure as Code for the `staging` and `production` environments (SPECIFICATIONS.md §57).
`local` is not a Terraform environment: it is the docker compose stack in `docker/compose.yaml`
(OQ-26).

## Layout

```text
infrastructure/terraform/
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
│   ├── iam/           # least-privilege Lambda + optional GitHub OIDC deploy role
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

Bootstrap the state bucket, DynamoDB lock table, and (once per account) GitHub OIDC provider
manually before the first apply. See `backend.hcl.example`.

## Working with an environment

```bash
cd infrastructure/terraform/envs/staging
cp terraform.tfvars.example terraform.tfvars   # edit; never commit secrets
terraform init -backend-config=backend.hcl
terraform fmt -recursive ../..
terraform validate
terraform plan -var-file=terraform.tfvars
# terraform apply is intentional and out of scope until CD is ready
```

CI runs `terraform fmt -check -recursive` and, per environment,
`terraform init -backend=false && terraform validate`, plus `tflint` and a Trivy configuration
scan of `infrastructure/terraform` (misconfigurations fail the job).

## Notes

- **No deploy from this change set.** Plan/validate only until CD and image pipelines land.
- **Lambda images:** push digest-tagged images to the environment ECR repos, then set
  `api_image_uri` / `worker_image_uri` before the first successful Lambda create/update.
- **Secrets:** JWT, DB URL, and Redis URL live only in Secrets Manager. Lambdas receive
  `APP_SECRETS_ARN` (not plaintext secrets in the function environment). Runtime must
  hydrate `JWT_SECRET` / `DATABASE_URL` / `REDIS_URL` from that secret at cold start.
- **ChromaDB (OQ-1):** `vector-store` does not provision a server; set `chroma_url` to an
  external endpoint (or leave readiness failing until hosting is decided).
- **GitHub OIDC:** create the identity provider in at most one environment per AWS account
  (production by default). Staging leaves `create_github_oidc = false`. Subject claims
  default to `main` and GitHub Environments (not `repo:*`).
- **Cost knobs:** staging disables interface VPC endpoints by default; production uses
  2 NAT gateways (not 3) and caps worker SQS concurrency.
