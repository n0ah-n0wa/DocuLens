# DocuLens developer entrypoints. Every target maps to the same commands CI runs
# (.github/workflows/ci.yml) so that a green `make check` predicts a green pipeline.

.DEFAULT_GOAL := help
COMPOSE := docker compose --project-directory . -f docker/compose.yaml

.PHONY: help bootstrap format format-check lint typecheck test test-e2e test-e2e-critical eval build docs-check check \
        check-ci db-upgrade db-downgrade db-revision docker-build docker-verify infra-up infra-down infra-logs \
        terraform-fmt terraform-validate terraform-lint terraform-check clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Install Python and Node dependencies and the Playwright browser
	uv sync --frozen
	pnpm install --frozen-lockfile
	pnpm --filter @doculens/web exec playwright install chromium

format: ## Apply formatting and safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .
	pnpm run format

format-check: ## Verify formatting without changing files
	uv run ruff format --check .
	pnpm run format:check

lint: ## Lint Python and TypeScript
	uv run ruff check .
	pnpm run lint

typecheck: ## Type-check Python (mypy --strict) and TypeScript (tsc, strict)
	uv run mypy
	pnpm run typecheck

test: ## Run Python tests and web unit tests
	uv run pytest
	pnpm run test:web

test-e2e: ## Run Playwright smoke e2e (requires `make build` first)
	pnpm run test:e2e

test-e2e-critical: ## Run critical Playwright suite (API + worker + infra must be up; `make build` first)
	pnpm run test:e2e:critical

eval: ## Run the deterministic RAG evaluation suite (SPECIFICATIONS.md §62)
	uv run python -m evals

db-upgrade: ## Apply all Alembic migrations to DATABASE_URL (from .env)
	uv run alembic -c packages/core/alembic.ini upgrade head

db-downgrade: ## Roll back the most recent Alembic migration
	uv run alembic -c packages/core/alembic.ini downgrade -1

db-revision: ## Autogenerate a migration from the ORM models: make db-revision m="add widgets"
	uv run alembic -c packages/core/alembic.ini revision --autogenerate -m "$(m)"

docs-check: ## Verify formatting and relative links of all Markdown documentation
	pnpm run format:check
	uv run python scripts/check_doc_links.py

build: ## Build the web application
	pnpm run build

check: format-check lint typecheck test build docs-check ## App quality gates (predicts CI backend+frontend app jobs)
check-ci: check terraform-check eval ## Broader local mirror of merge gates (needs terraform + tflint)

docker-build: ## Build the API and worker images (cached layers OK for local use)
	docker build -f docker/api.Dockerfile -t doculens-api:local .
	docker build -f docker/worker.Dockerfile -t doculens-worker:local .

docker-verify: ## Clean-build production images and run hardening smoke checks (§56)
	docker build --no-cache --pull -f docker/api.Dockerfile -t doculens-api:verify .
	docker build --no-cache --pull -f docker/worker.Dockerfile -t doculens-worker:verify .
	uv run python scripts/verify_production_images.py

infra-up: ## Start local PostgreSQL, Redis, ChromaDB and MinIO, then create the documents bucket
	$(COMPOSE) up --detach --wait
	$(COMPOSE) run --rm minio-init

infra-down: ## Stop the local services (data volumes are kept)
	$(COMPOSE) down

infra-logs: ## Tail local service logs
	$(COMPOSE) logs --follow

terraform-fmt: ## Check Terraform formatting (CI gate)
	terraform fmt -check -recursive infrastructure/terraform

terraform-validate: ## Init without backend and validate staging + production
	cd infrastructure/terraform/envs/staging && terraform init -backend=false -input=false && terraform validate
	cd infrastructure/terraform/envs/production && terraform init -backend=false -input=false && terraform validate

terraform-lint: ## Run tflint against staging and production roots
	cd infrastructure/terraform && tflint --init --config .tflint.hcl
	cd infrastructure/terraform && tflint --config .tflint.hcl --chdir envs/staging --call-module-type=local
	cd infrastructure/terraform && tflint --config .tflint.hcl --chdir envs/production --call-module-type=local

terraform-check: terraform-fmt terraform-validate terraform-lint ## Terraform format, validate, and lint

clean: ## Remove build artefacts and caches
	rm -rf .venv .mypy_cache .pytest_cache .ruff_cache apps/web/.next apps/web/playwright-report apps/web/test-results dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
