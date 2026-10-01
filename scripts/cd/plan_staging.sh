#!/usr/bin/env bash
# Terraform plan for staging (no apply). Expects OIDC/AWS creds already configured.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${AWS_REGION:?}"
: "${TF_STATE_BUCKET:?}"
: "${TF_LOCK_TABLE:?}"

TF_DIR="${ROOT}/infrastructure/terraform/envs/staging"
cat > "${TF_DIR}/backend.hcl" <<EOF
bucket         = "${TF_STATE_BUCKET}"
region         = "${AWS_REGION}"
dynamodb_table = "${TF_LOCK_TABLE}"
encrypt        = true
EOF

cd "${TF_DIR}"
terraform init -input=false -backend-config=backend.hcl
terraform plan -input=false -out="${ROOT}/.local/staging.tfplan"
terraform show -no-color "${ROOT}/.local/staging.tfplan" > "${ROOT}/.local/staging.tfplan.txt"
echo "Wrote .local/staging.tfplan"
