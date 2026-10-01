# Terraform modules

Reusable modules for staging and production (SPECIFICATIONS.md §57–§58).

| Module          | Responsibility                                                                 |
| --------------- | ------------------------------------------------------------------------------ |
| `networking`    | VPC, public/private subnets, NAT, security groups, Gateway/Interface endpoints |
| `kms`           | Customer-managed CMK (rotation on) for S3, SQS, Secrets, logs                  |
| `ecr`           | Immutable-tag ECR repos for API and worker images                              |
| `storage`       | Documents S3 bucket (SSE-KMS, versioning, public access blocked)               |
| `database`      | RDS PostgreSQL (private, encrypted, SSL forced)                                |
| `cache`         | ElastiCache Redis (AUTH, TLS, encryption at rest)                              |
| `queue`         | SQS processing queue + DLQ (KMS)                                               |
| `secrets`       | Secrets Manager app bundle (generated JWT / DB / Redis; optional extras)       |
| `iam`           | Least-privilege Lambda roles + GitHub OIDC IdP / per-env deploy role           |
| `frontend`      | S3 + CloudFront static Next.js hosting (OQ-19 provisional)                     |
| `compute`       | Container-image Lambdas (API, SQS worker, migrations) in the VPC               |
| `api-gateway`   | HTTP API (`$default` → Lambda proxy) with access logs and throttling           |
| `observability` | Encrypted CloudWatch log groups and error/DLQ alarms                           |
| `vector-store`  | Placeholder for ChromaDB hosting (**OQ-1** still open)                         |

Open questions that intentionally leave gaps:

- **OQ-1** — Chroma hosting (ECS/EC2/Cloud); SG reserved, no compute yet.
- **OQ-2** — Worker packaging / Lambda RIC vs ECS; functions accept container images.
- **OQ-3b** — SSE through API Gateway; HTTP API is provisional.
- **OQ-23** — Migrations Lambda exists for CD to invoke; runner entrypoint TBD.
