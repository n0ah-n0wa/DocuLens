#!/usr/bin/env bash
# Build and push API + worker images to ECR, then apply production Terraform with digests,
# sync the static frontend, invalidate CloudFront, and invoke migrations.
#
# Required env (set by CD · production; never pass application secrets here):
#   AWS_REGION, IMAGE_TAG (git sha), ECR_REGISTRY
#   TF_STATE_BUCKET, TF_LOCK_TABLE (remote state)
# Optional:
#   TF_VAR_extra_secret_values  — JSON object for Secrets Manager extras (from GitHub
#                                 Environment secret only; never commit values)
#   SKIP_IMAGE_BUILD=1          — require pre-tagged images (CD path)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"
mkdir -p "${ROOT}/.local"

: "${AWS_REGION:?}"
: "${IMAGE_TAG:?}"
: "${ECR_REGISTRY:?}"
: "${TF_STATE_BUCKET:?}"
: "${TF_LOCK_TABLE:?}"

API_REPO="${ECR_REGISTRY}/doculens-production-api"
WORKER_REPO="${ECR_REGISTRY}/doculens-production-worker"
API_TAG="${API_REPO}:${IMAGE_TAG}"
WORKER_TAG="${WORKER_REPO}:${IMAGE_TAG}"
TF_DIR="${ROOT}/infrastructure/terraform/envs/production"

image_present() {
  docker image inspect "$1" >/dev/null 2>&1
}

if image_present "${API_TAG}" && image_present "${WORKER_TAG}"; then
  echo "==> Using prebuilt images ${API_TAG} and ${WORKER_TAG}"
elif [[ "${SKIP_IMAGE_BUILD:-0}" == "1" ]]; then
  echo "SKIP_IMAGE_BUILD=1 but images missing: ${API_TAG} / ${WORKER_TAG}" >&2
  exit 1
else
  echo "==> Building API image ${API_TAG}"
  docker build -f docker/api.Dockerfile -t "${API_TAG}" .
  echo "==> Building worker image ${WORKER_TAG}"
  docker build -f docker/worker.Dockerfile -t "${WORKER_TAG}" .
fi

echo "==> Pushing images"
docker push "${API_TAG}"
docker push "${WORKER_TAG}"

API_DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${API_TAG}")"
WORKER_DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${WORKER_TAG}")"
echo "API digest: ${API_DIGEST}"
echo "Worker digest: ${WORKER_DIGEST}"

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

echo "==> Snapshot current production digests for rollback"
(
  cd "${TF_DIR}"
  terraform init -input=false -backend-config=backend.hcl >/dev/null
)
PREV_API_DIGEST=""
PREV_WORKER_DIGEST=""
PREV_API_FN=""
PREV_WORKER_FN=""
PREV_FRONTEND_BUCKET=""
PREV_DISTRIBUTION_ID=""
if PREV_API_FN="$(cd "${TF_DIR}" && terraform output -raw api_lambda_function_name 2>/dev/null)"; then
  PREV_API_DIGEST="$(aws lambda get-function --function-name "${PREV_API_FN}" \
    --query 'Code.ImageUri' --output text 2>/dev/null || true)"
  PREV_WORKER_FN="$(cd "${TF_DIR}" && terraform output -raw worker_lambda_function_name)"
  PREV_WORKER_DIGEST="$(aws lambda get-function --function-name "${PREV_WORKER_FN}" \
    --query 'Code.ImageUri' --output text 2>/dev/null || true)"
  PREV_FRONTEND_BUCKET="$(cd "${TF_DIR}" && terraform output -raw frontend_bucket_id)"
  PREV_DISTRIBUTION_ID="$(cd "${TF_DIR}" && terraform output -raw frontend_distribution_id)"
fi
{
  echo "PREV_API_DIGEST=${PREV_API_DIGEST}"
  echo "PREV_WORKER_DIGEST=${PREV_WORKER_DIGEST}"
  echo "PREV_API_FUNCTION=${PREV_API_FN}"
  echo "PREV_WORKER_FUNCTION=${PREV_WORKER_FN}"
  echo "PREV_FRONTEND_BUCKET=${PREV_FRONTEND_BUCKET}"
  echo "PREV_DISTRIBUTION_ID=${PREV_DISTRIBUTION_ID}"
  echo "PREV_IMAGE_TAG_NOTE=Restore Lambda digests with rollback_production.sh; full stack rollback redeploys a prior git SHA."
} > "${ROOT}/.local/production-pre-deploy.env"
echo "Wrote .local/production-pre-deploy.env"

echo "==> Terraform init / apply (image digests; secrets stay in Secrets Manager)"
(
  cd "${TF_DIR}"
  terraform init -input=false -backend-config=backend.hcl
  terraform apply -input=false -auto-approve \
    -var="api_image_uri=${API_DIGEST}" \
    -var="worker_image_uri=${WORKER_DIGEST}"
)

FRONTEND_BUCKET="$(cd "${TF_DIR}" && terraform output -raw frontend_bucket_id)"
DISTRIBUTION_ID="$(cd "${TF_DIR}" && terraform output -raw frontend_distribution_id)"
API_URL="$(cd "${TF_DIR}" && terraform output -raw api_gateway_endpoint)"
FRONTEND_URL="$(cd "${TF_DIR}" && terraform output -raw frontend_url)"
MIGRATE_FN="$(cd "${TF_DIR}" && terraform output -raw migrate_lambda_function_name)"

echo "==> Building static frontend (NEXT_PUBLIC_API_BASE_URL=${API_URL})"
export DOCULENS_STATIC_EXPORT=1
export NEXT_PUBLIC_API_BASE_URL="${API_URL}"
pnpm install --frozen-lockfile
pnpm --filter @doculens/web run build

echo "==> Sync frontend to s3://${FRONTEND_BUCKET}"
aws s3 sync "${ROOT}/apps/web/out/" "s3://${FRONTEND_BUCKET}/" --delete --only-show-errors

echo "==> Invalidate CloudFront ${DISTRIBUTION_ID}"
aws cloudfront create-invalidation --distribution-id "${DISTRIBUTION_ID}" --paths "/*" >/dev/null

echo "==> Invoke migrations Lambda ${MIGRATE_FN}"
INVOKE_OUT="${ROOT}/.local/migrate-invoke.json"
PAYLOAD_OUT="${ROOT}/.local/migrate-response.json"
aws lambda invoke \
  --function-name "${MIGRATE_FN}" \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  "${PAYLOAD_OUT}" \
  > "${INVOKE_OUT}"
INVOKE_STATUS="$(python -c "import json; print(json.load(open(r'${INVOKE_OUT}', encoding='utf-8')).get('StatusCode',''))")"
FUNCTION_ERROR="$(python -c "import json; print(json.load(open(r'${INVOKE_OUT}', encoding='utf-8')).get('FunctionError') or '')")"
echo "Migrate invoke StatusCode=${INVOKE_STATUS} FunctionError=${FUNCTION_ERROR:-none}"
if [ "${INVOKE_STATUS}" != "200" ] || [ -n "${FUNCTION_ERROR}" ]; then
  echo "Migration Lambda invoke failed" >&2
  cat "${INVOKE_OUT}" >&2 || true
  cat "${PAYLOAD_OUT}" >&2 || true
  exit 1
fi
python - <<'PY'
import json
from pathlib import Path
body = json.loads(Path(".local/migrate-response.json").read_text(encoding="utf-8"))
if not isinstance(body, dict) or body.get("status") != "ok":
    raise SystemExit(f"migrate payload unexpected: {body!r}")
print("migrate payload ok")
PY

{
  echo "API_URL=${API_URL}"
  echo "FRONTEND_URL=${FRONTEND_URL}"
  echo "API_DIGEST=${API_DIGEST}"
  echo "WORKER_DIGEST=${WORKER_DIGEST}"
  echo "IMAGE_TAG=${IMAGE_TAG}"
  echo "FRONTEND_BUCKET=${FRONTEND_BUCKET}"
  echo "DISTRIBUTION_ID=${DISTRIBUTION_ID}"
} > "${ROOT}/.local/production-deploy.env"

echo "==> Production deploy complete"
echo "    API:      ${API_URL}"
echo "    Frontend: ${FRONTEND_URL}"
echo "    Rollback snapshot: .local/production-pre-deploy.env"
