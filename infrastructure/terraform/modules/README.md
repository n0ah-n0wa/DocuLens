# Terraform modules

Planned modules (SPECIFICATIONS.md §57), added in the infrastructure phase once the hosting
decisions in `docs/planning/open-questions.md` (OQ-1, OQ-2, OQ-3b, OQ-19, OQ-23) are made:

| Module          | Responsibility                                                    |
| --------------- | ----------------------------------------------------------------- |
| `networking`    | VPC, subnets, security groups, VPC endpoints                      |
| `compute`       | Lambda functions (API, worker, migrations) and their packaging    |
| `database`      | RDS/Aurora PostgreSQL, parameter groups, credentials wiring       |
| `storage`       | S3 buckets for documents (encryption, lifecycle, access policies) |
| `cache`         | ElastiCache Redis                                                 |
| `vector-store`  | ChromaDB hosting (pending OQ-1)                                   |
| `iam`           | Least-privilege roles for API, worker, deployment and monitoring  |
| `secrets`       | Secrets Manager entries and access policies                       |
| `observability` | CloudWatch log groups, metrics, alarms, tracing                   |
| `api-gateway`   | API Gateway, routes, throttling, custom domain                    |

No module code exists yet; this file exists so the directory and its intent are versioned.
