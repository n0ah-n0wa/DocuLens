#!/usr/bin/env bash
# Terraform plan for production (no apply). Expects OIDC/AWS creds already configured.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${AWS_REGION:?}"
: "${TF_STATE_BUCKET:?}"
: "${TF_LOCK_TABLE:?}"

TF_DIR="${ROOT}/infrastructure/terraform/envs/production"
mkdir -p "${ROOT}/.local"
BACKEND_KMS_LINE=""
if [[ -n "${TF_STATE_KMS_KEY_ID:-}" ]]; then
  BACKEND_KMS_LINE="kms_key_id     = \"${TF_STATE_KMS_KEY_ID}\""
fi
cat > "${TF_DIR}/backend.hcl" <<EOF
bucket         = "${TF_STATE_BUCKET}"
region         = "${AWS_REGION}"
dynamodb_table = "${TF_LOCK_TABLE}"
encrypt        = true
${BACKEND_KMS_LINE}
EOF

cd "${TF_DIR}"
terraform init -input=false -backend-config=backend.hcl
terraform plan -input=false -out="${ROOT}/.local/production.tfplan"
terraform show -no-color "${ROOT}/.local/production.tfplan" > "${ROOT}/.local/production.tfplan.txt"
echo "Wrote .local/production.tfplan"
