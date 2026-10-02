#!/usr/bin/env bash
# Fast production rollback of API + worker Lambda container images to the digests
# captured immediately before the last deploy_production.sh apply.
#
# This does NOT:
#   - reverse Alembic migrations (schema stays forward; see docs/deployment.md)
#   - restore a previous frontend build (re-deploy a prior git SHA for that)
#
# Required env:
#   AWS_REGION, TF_STATE_BUCKET, TF_LOCK_TABLE
# Optional:
#   ROLLBACK_ENV_FILE  — default .local/production-pre-deploy.env
#   SKIP_FRONTEND=1    — always skip frontend (default: skip unless FRONTEND_ROLLBACK_DIR set)
#   FRONTEND_ROLLBACK_DIR — local directory of static assets to sync (previous out/)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

: "${AWS_REGION:?}"
: "${TF_STATE_BUCKET:?}"
: "${TF_LOCK_TABLE:?}"

ROLLBACK_ENV_FILE="${ROLLBACK_ENV_FILE:-${ROOT}/.local/production-pre-deploy.env}"
if [[ ! -f "${ROLLBACK_ENV_FILE}" ]]; then
  echo "Missing rollback snapshot ${ROLLBACK_ENV_FILE}" >&2
  echo "Run a production deploy first, or download the production-pre-deploy artifact from CD." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${ROLLBACK_ENV_FILE}"

: "${PREV_API_DIGEST:?PREV_API_DIGEST empty — nothing to roll back to (first deploy?)}"
: "${PREV_WORKER_DIGEST:?PREV_WORKER_DIGEST empty — nothing to roll back to (first deploy?)}"

if [[ "${PREV_API_DIGEST}" == "None" || "${PREV_API_DIGEST}" == "null" || -z "${PREV_API_DIGEST}" ]]; then
  echo "PREV_API_DIGEST is unset; cannot roll back Lambdas." >&2
  exit 1
fi
if [[ "${PREV_WORKER_DIGEST}" == "None" || "${PREV_WORKER_DIGEST}" == "null" || -z "${PREV_WORKER_DIGEST}" ]]; then
  echo "PREV_WORKER_DIGEST is unset; cannot roll back Lambdas." >&2
  exit 1
fi

TF_DIR="${ROOT}/infrastructure/terraform/envs/production"
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

echo "==> Rolling back production Lambdas to previous digests"
echo "    API:    ${PREV_API_DIGEST}"
echo "    Worker: ${PREV_WORKER_DIGEST}"

(
  cd "${TF_DIR}"
  terraform init -input=false -backend-config=backend.hcl
  terraform apply -input=false -auto-approve \
    -var="api_image_uri=${PREV_API_DIGEST}" \
    -var="worker_image_uri=${PREV_WORKER_DIGEST}"
)

API_URL="$(cd "${TF_DIR}" && terraform output -raw api_gateway_endpoint)"
FRONTEND_URL="$(cd "${TF_DIR}" && terraform output -raw frontend_url)"
FRONTEND_BUCKET="$(cd "${TF_DIR}" && terraform output -raw frontend_bucket_id)"
DISTRIBUTION_ID="$(cd "${TF_DIR}" && terraform output -raw frontend_distribution_id)"

if [[ -n "${FRONTEND_ROLLBACK_DIR:-}" ]]; then
  if [[ ! -d "${FRONTEND_ROLLBACK_DIR}" ]]; then
    echo "FRONTEND_ROLLBACK_DIR=${FRONTEND_ROLLBACK_DIR} is not a directory" >&2
    exit 1
  fi
  echo "==> Syncing frontend rollback assets from ${FRONTEND_ROLLBACK_DIR}"
  aws s3 sync "${FRONTEND_ROLLBACK_DIR}/" "s3://${FRONTEND_BUCKET}/" --delete --only-show-errors
  aws cloudfront create-invalidation --distribution-id "${DISTRIBUTION_ID}" --paths "/*" >/dev/null
elif [[ "${SKIP_FRONTEND:-1}" == "1" ]]; then
  echo "==> Skipping frontend rollback (set FRONTEND_ROLLBACK_DIR to sync previous assets,"
  echo "    or re-run CD · production on the prior git SHA for a full application rollback)."
fi

{
  echo "API_URL=${API_URL}"
  echo "FRONTEND_URL=${FRONTEND_URL}"
  echo "API_DIGEST=${PREV_API_DIGEST}"
  echo "WORKER_DIGEST=${PREV_WORKER_DIGEST}"
  echo "ROLLED_BACK=1"
} > "${ROOT}/.local/production-deploy.env"

echo "==> Lambda rollback complete"
echo "    API:      ${API_URL}"
echo "    Frontend: ${FRONTEND_URL}"
echo "    Schema:   unchanged (migrations are forward-only; see docs/deployment.md)"
echo "    Full SHA rollback: workflow_dispatch CD · production on the previous commit after staging green."
