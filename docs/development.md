# Local development

## Setting up a clean machine

| Tool      | Version         | Notes                                                                  |
| --------- | --------------- | ---------------------------------------------------------------------- |
| uv        | ≥ 0.12          | Installs the pinned Python (`.python-version`) and all Python packages |
| Node.js   | 24.20.0         | Pinned in `.node-version`; `corepack enable` provides the pinned pnpm  |
| pnpm      | 11.22.0         | Pinned in `package.json` (`packageManager`)                            |
| Docker    | with Compose v2 | Local PostgreSQL, Redis, ChromaDB; image builds                        |
| GNU make  | any             | Task runner; on Windows use Git Bash or WSL, or run the commands below |
| Terraform | ~> 1.16         | Only for `infrastructure/terraform`                                    |

```bash
git clone <repository-url> doculens && cd doculens
make bootstrap                 # uv sync --frozen; pnpm install --frozen-lockfile; playwright install chromium
cp .env.example .env           # local-only values; never commit .env
make infra-up                  # postgres, redis, chroma (docker compose)
make check                     # everything CI runs, except Docker and Terraform jobs
```

Without make, the underlying commands are:

```bash
uv sync --frozen
pnpm install --frozen-lockfile
pnpm --filter @doculens/web exec playwright install chromium

uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest
pnpm run format:check && pnpm run lint && pnpm run typecheck && pnpm run build
pnpm run test:e2e              # after pnpm run build
```

## Running the services

```bash
uv run uvicorn doculens_api.main:app --reload --port 8000     # API + OpenAPI at /docs
uv run python -m doculens_worker                              # worker (no handlers yet)
pnpm --filter @doculens/web dev                               # web app on :3000
docker compose --project-directory . -f docker/compose.yaml up --detach --wait
```

## Workspace layout

Python is a single uv workspace (`pyproject.toml` at the root, one `uv.lock`, one `.venv`):

| Package           | Path                       | Import name       | Depends on    |
| ----------------- | -------------------------- | ----------------- | ------------- |
| `doculens-core`   | `packages/core`            | `doculens`        | —             |
| `doculens-api`    | `apps/api`                 | `doculens_api`    | core, FastAPI |
| `doculens-worker` | `services/document-worker` | `doculens_worker` | core          |

Node is a pnpm workspace (`pnpm-workspace.yaml`): `apps/web` and `packages/shared-types`.

## Conventions

- **Layer rules** (SPECIFICATIONS.md §72) are enforced by `packages/core/tests/unit/test_architecture.py`
  and by packaging: the core package cannot import FastAPI because it does not depend on it.
- **Tests** live next to each package (`<package>/tests/{unit,integration,api,adversarial}`) and are
  marked with the pytest markers declared in the root `pyproject.toml`. Test directories are not
  Python packages, so test module basenames must be unique across the workspace (mypy checks every
  package in one run).
- **Formatting and lint** are non-negotiable gates: `ruff format`, `ruff check`, `mypy --strict`,
  Prettier, ESLint, strict TypeScript. Never suppress a rule to make a check pass without a comment
  explaining why.
- **Dependencies** are pinned exactly and added only in the phase that uses them, following the
  checklist in SPECIFICATIONS.md §84. Add Python packages with `uv add --package <member> <name>==<ver>`
  and Node packages with `pnpm --filter <workspace> add -E <name>@<ver>`; commit the updated lockfile.
- **Commits** use the prefixes in SPECIFICATIONS.md §85 (`feat:`, `fix:`, `test:`, `docs:`, `chore:`,
  `ci:`, `infra:`, `security:`), small and atomic.
- **Specification changes** are never silent: record deviations in an ADR and in
  `docs/planning/open-questions.md`.

## Troubleshooting

- `pnpm install` refuses to run: Node ≥ 24 and pnpm ≥ 11 are required (`engine-strict`). Run
  `corepack enable` to get the pinned pnpm.
- Playwright cannot find a browser: `pnpm --filter @doculens/web exec playwright install chromium`.
- `uv run mypy` reports duplicate modules: two test files share a basename; rename one.
