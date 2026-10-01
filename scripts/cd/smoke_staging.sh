#!/usr/bin/env bash
# Run the staging smoke suite against a live stack.
# Requires API_URL + FRONTEND_URL (or .local/staging-deploy.env from deploy_staging.sh).
# Creates controlled fixture data and deletes it before exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

if [[ -f "${ROOT}/.local/staging-deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/.local/staging-deploy.env"
fi

: "${API_URL:?Set API_URL or run deploy_staging.sh first}"
: "${FRONTEND_URL:?Set FRONTEND_URL or run deploy_staging.sh first}"

export API_URL="${API_URL%/}"
export FRONTEND_URL="${FRONTEND_URL%/}"

echo "==> Staging smoke (API + frontend + auth + upload + process + index + RAG + citations + delete)"
uv run python scripts/cd/smoke_staging.py
