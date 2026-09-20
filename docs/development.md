# Development guide

How to set up a machine, run the services, keep the quality gates green and add features without
breaking the architecture described in [`architecture.md`](architecture.md).
[`SPECIFICATIONS.md`](../SPECIFICATIONS.md) is the source of truth for every requirement.

## 1. Setting up a clean machine

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
make infra-up                  # postgres, redis, chroma (docker compose, loopback only)
make check                     # everything CI runs, except the Docker and Terraform jobs
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

Line endings are forced to LF on every platform by `.gitattributes`; editors pick up
`.editorconfig`. VS Code users get extension recommendations from `.vscode/extensions.json`.

## 2. Running the services

```bash
uv run uvicorn doculens_api.main:create_app --factory --reload --port 8000     # API + OpenAPI at /docs
uv run python -m doculens_worker                              # worker (no job handlers yet)
pnpm --filter @doculens/web dev                               # web app on :3000
docker compose --project-directory . -f docker/compose.yaml up --detach --wait
make docker-build                                             # production images, built locally
```

Environments (§87): `local` is the docker compose stack; `staging` and `production` are Terraform
roots (see `infrastructure/terraform/README.md`). Local development never requires AWS.

### Database and migrations

The schema is owned by the ORM models in `packages/core/src/doculens/infrastructure/persistence/models.py`
and changed only through Alembic (§45):

```bash
make db-upgrade                       # apply migrations to DATABASE_URL from .env
make db-revision m="add usage ledger" # autogenerate a migration after changing the models; review it
make db-downgrade                     # roll back one revision
```

CI proves that the migration chain and the models describe the same schema, so an un-migrated
model change fails the build. If port 5432 is unavailable on your machine, set `POSTGRES_PORT`
(and the port inside `DATABASE_URL`) in `.env`.

Integration tests (`packages/core/tests/integration`, marker `integration`) start a disposable
PostgreSQL container through testcontainers; set `DOCULENS_TEST_DATABASE_URL` to reuse a running
server instead. **The tests truncate every table of that database and create a second database
next to it for migration tests, so never point it at data you want to keep.** Without Docker they
are skipped locally and fail in CI.

## 3. Workspace layout

Python is one uv workspace (`pyproject.toml` at the root, one `uv.lock`, one `.venv`):

| Package           | Path                       | Import name       | Depends on    | Contains                                  |
| ----------------- | -------------------------- | ----------------- | ------------- | ----------------------------------------- |
| `doculens-core`   | `packages/core`            | `doculens`        | —             | `domain`, `application`, `infrastructure` |
| `doculens-api`    | `apps/api`                 | `doculens_api`    | core, FastAPI | routers, schemas, middleware, app factory |
| `doculens-worker` | `services/document-worker` | `doculens_worker` | core          | queue consumer entrypoint                 |

Node is one pnpm workspace (`pnpm-workspace.yaml`): `apps/web` (Next.js) and
`packages/shared-types` (TypeScript API contracts).

Where new code goes:

| You are adding…                                      | Put it in                                                             |
| ---------------------------------------------------- | --------------------------------------------------------------------- |
| an entity, value object, state machine, port         | `doculens.domain`                                                     |
| a use case or orchestration (e.g. `RAGService`)      | `doculens.application`                                                |
| an adapter (SQLAlchemy, S3, Chroma, Redis, provider) | `doculens.infrastructure`                                             |
| an HTTP route, request/response schema, middleware   | `doculens_api`                                                        |
| a queue handler                                      | `doculens_worker`                                                     |
| a UI screen or component                             | `apps/web/src`                                                        |
| a type shared with the frontend                      | `packages/shared-types` (generated from OpenAPI once endpoints exist) |

## 4. Quality gates

`make check` runs, in order: `ruff format --check`, Prettier check, `ruff check`, ESLint,
`mypy --strict`, `tsc --noEmit`, `pytest` (branch coverage, 90% floor), `next build`, and
`make docs-check` (Prettier plus `scripts/check_doc_links.py`, which fails on any broken relative
Markdown link).
CI additionally builds and scans the Docker images, runs gitleaks, pip-audit and pnpm audit, and
validates Terraform. A pull request is mergeable only when all of it is green (§59, §82).

Rules that are easy to trip over:

- **Layer rules** are enforced by `packages/core/tests/unit/test_architecture.py`: `domain` and
  `application` may not import FastAPI, boto3, ChromaDB, LangChain, SQLAlchemy, Redis or outer
  layers.
- **Test module basenames must be unique across packages.** Test directories are not packages, and
  mypy checks every package in one run.
- **Markers are strict.** Use `unit`, `integration`, `api` or `adversarial` (declared in the root
  `pyproject.toml`); unknown markers fail the run.
- **Never suppress a rule silently.** A `noqa`, `type: ignore[code]` or ESLint disable needs a
  reason on the same line, and unused suppressions fail lint.

## 5. Adding a feature (§80–§82)

1. Read the relevant sections of `SPECIFICATIONS.md` and check
   `docs/planning/open-questions.md`; if the feature touches an undecided `OQ-n`, get the decision
   first and record it (see [`decisions/README.md`](decisions/README.md)).
2. Look at existing patterns in the layer you are changing and at the tests around it.
3. Plan the smallest coherent change; implement it in the correct layer.
4. Add or update tests at the right level (unit for domain and application logic, API tests for
   routes, integration tests for adapters, adversarial tests for abuse cases).
5. Verify authorization for every user-owned resource you touch; use the standard error envelope.
6. Update documentation where the architecture or configuration changed.
7. Run `make check`, review the diff, and report any deviation from the specification.

A feature is done only when implementation, tests, error handling, authorization, documentation and
all gates are complete (§82).

## 6. Dependencies (§84)

Before adding a dependency, confirm the standard library or an existing dependency does not cover
it, that the package is maintained and has acceptable security characteristics, and that it
meaningfully reduces complexity. Then:

```bash
uv add --package doculens-core "name==x.y.z"     # or doculens-api / doculens-worker
uv add --group dev "name==x.y.z"                  # tooling shared by all packages
pnpm --filter @doculens/web add -E name@x.y.z     # exact version is enforced by .npmrc
```

Commit the updated lockfile with the manifest. pnpm refuses versions published less than 24 hours
ago (`minimumReleaseAge`) and blocks dependency build scripts unless allow-listed in
`pnpm-workspace.yaml`.

## 7. Configuration and secrets (§54, §70)

- `.env.example` (backend and compose) and `apps/web/.env.example` document every variable; real
  values go in git-ignored `.env` / `.env.local` files locally and in AWS Secrets Manager when deployed.
- Never commit secrets; CI runs gitleaks. Never log passwords, tokens, API keys or full document text.
- Configuration is validated at start-up once the settings module exists; invalid production
  configuration must fail loudly.

## 8. Commits and pull requests (§85–§86)

Small, atomic commits with the prefixes `feat: fix: refactor: test: docs: chore: ci: infra: security:`.
The pull request template carries the definition-of-done checklist and a section for specification
deviations. `main` is protected: green CI and review before merge, no force pushes.

## 9. Troubleshooting

- `pnpm install` refuses to run: Node 24 and pnpm 11 are required (`engine-strict`); run
  `corepack enable`.
- `pnpm install` reports a supply-chain policy violation: a pinned version is newer than 24 hours.
  Wait or choose the previous release.
- Playwright cannot find a browser: `pnpm --filter @doculens/web exec playwright install chromium`.
- `uv run mypy` reports duplicate modules: two test files share a basename; rename one.
- Coverage below the floor: the new code has no test, or a subprocess-only path is not excluded.
- `docker info` fails on Windows: start Docker Desktop before `make infra-up` or `make docker-build`.
