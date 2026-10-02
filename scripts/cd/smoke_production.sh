#!/usr/bin/env bash
# Run the production smoke suite against a live stack.
# Requires API_URL + FRONTEND_URL (or .local/production-deploy.env from deploy_production.sh).
# Creates controlled fixture data and deletes it before exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

if [[ -f "${ROOT}/.local/production-deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.local/production-deploy.env"
fi

: "${API_URL:?Set API_URL or run deploy_production.sh first}"
: "${FRONTEND_URL:?Set FRONTEND_URL or run deploy_production.sh first}"

export API_URL="${API_URL%/}"
export FRONTEND_URL="${FRONTEND_URL%/}"
export DOCULENS_DEPLOY_ENV_FILE="${ROOT}/.local/production-deploy.env"
export SMOKE_EMAIL_PREFIX="${SMOKE_EMAIL_PREFIX:-production-smoke}"
export SMOKE_LABEL="${SMOKE_LABEL:-Production}"

echo "==> Production smoke (API + frontend + auth + upload + process + index + RAG + citations + delete)"
uv run python scripts/cd/smoke_staging.py
