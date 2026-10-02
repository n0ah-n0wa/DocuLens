# Terraform remote state bootstrap (manual, one-time)

Creates the S3 state bucket (versioned, SSE-KMS, public access blocked, TLS deny) and
DynamoDB lock table used by `envs/staging` and `envs/production`.

**Do not** wire this stack into CD plan/apply. Operators apply it once from a trusted
admin role, then configure GitHub Environment variables / `backend.hcl`.

## Apply

```bash
cd infrastructure/terraform/bootstrap/state
terraform init
terraform apply -var="aws_region=eu-central-1"
```

Record outputs:

| Output            | Use as                                             |
| ----------------- | -------------------------------------------------- |
| `state_bucket_id` | `TF_STATE_BUCKET` / `backend.hcl` `bucket`         |
| `lock_table_name` | `TF_LOCK_TABLE` / `backend.hcl` `dynamodb_table`   |
| `kms_key_arn`     | `TF_STATE_KMS_KEY_ID` / `backend.hcl` `kms_key_id` |

Staging and production share one bucket with distinct state keys
(`doculens/staging/terraform.tfstate`, `doculens/production/terraform.tfstate`).
