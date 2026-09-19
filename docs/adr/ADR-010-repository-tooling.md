# ADR-010 — Repository tooling

**Status:** Accepted
**Date:** 2026-09-19
**Specification references:** §5.1, §5.2, §5.5, §55, §59, §81, §84, §85

## Context

The specification requires pinned versions, strict typing, formatting/lint/type/test gates on every
pull request, dependency and secret scanning, and a repository that can be initialised
reproducibly. It names the libraries but not the package managers or the workspace layout.

## Decision

| Concern          | Choice                                                                                                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python packaging | uv workspace; one `uv.lock`; `uv_build` backend; `.python-version` 3.12                                                                                                                           |
| Python pins      | Exact `==` in every `pyproject.toml` plus the lockfile                                                                                                                                            |
| Python quality   | `ruff format`, `ruff check` (broad rule set incl. bandit `S`, pylint, pytest style), `mypy --strict` with the pydantic plugin                                                                     |
| Python tests     | pytest with `--import-mode=importlib`, one root invocation, markers per test layer, branch coverage with `fail_under = 90`; `httpx2` for the test client (Starlette 1.6 deprecates `httpx` there) |
| Node packaging   | pnpm workspace; `packageManager` pinned; `.node-version` pinned; `save-exact`; `engine-strict`; `minimumReleaseAge` 24 h; dependency build scripts allow-listed                                   |
| TypeScript       | 5.9.x with a shared strict `tsconfig.base.json` (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `verbatimModuleSyntax`, `noImplicitReturns`, …)                                        |
| Web quality      | ESLint (`eslint-config-next`), Prettier, `tsc --noEmit`                                                                                                                                           |
| Web tests        | Playwright end-to-end; no unit-test runner until pure logic exists (OQ-20)                                                                                                                        |
| Images           | Docker tags pinned to exact versions; uv binary copied from its pinned image                                                                                                                      |
| Infrastructure   | Terraform `~> 1.16`, AWS provider exact; `fmt -check` and `validate` in CI                                                                                                                        |
| CI               | GitHub Actions pinned to commit SHAs; Dependabot for actions, uv, npm, docker, terraform                                                                                                          |
| Task runner      | GNU make mirroring the CI commands                                                                                                                                                                |

## Alternatives considered

- **pip-tools / Poetry** for Python: both work, but uv provides workspaces, Python installation and
  a single lockfile with far fewer moving parts.
- **TypeScript 7 (native compiler)**: available on npm, but the ESLint and Next.js tool-chain
  compatibility is not yet established; revisit when `eslint-config-next` declares support.
- **npm / yarn workspaces**: pnpm's strict `node_modules` layout prevents phantom dependencies.
- **Version ranges in manifests with lockfile-only pinning**: rejected to satisfy §5.1 literally
  and to make upgrades explicit in review.

## Consequences

- Dependabot pull requests are the upgrade path; manual edits must update both manifest and lockfile.
- Test module basenames must be unique across Python packages because mypy checks all packages in
  one run and test directories are not packages.
- Developers on Windows need Git Bash or WSL for `make`; the equivalent commands are documented in
  `docs/development.md`.
