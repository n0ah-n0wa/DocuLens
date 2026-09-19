# ADR-001 — Backend architecture

**Status:** Accepted
**Date:** 2026-09-19
**Specification references:** §4, §71, §72, §73, §74, §83

## Context

The specification requires a layered or hexagonal backend in which domain logic does not depend on
FastAPI, the AWS SDK, ChromaDB, LangChain or PostgreSQL implementation details, and in which the
API and the document worker are separate deployable components sharing that logic. It also asks
that abstractions correspond to real architectural boundaries and that unnecessary abstraction be
avoided.

## Decision

The backend is split into three Python distributions in one uv workspace:

| Distribution      | Path                       | Contents                                                             |
| ----------------- | -------------------------- | -------------------------------------------------------------------- |
| `doculens-core`   | `packages/core`            | `doculens.domain`, `doculens.application`, `doculens.infrastructure` |
| `doculens-api`    | `apps/api`                 | FastAPI routers, schemas, middleware, dependency wiring              |
| `doculens-worker` | `services/document-worker` | Queue consumer and job dispatch                                      |

Dependency direction inside the core: `application → domain`; `infrastructure` implements the
ports (`typing.Protocol`) declared by `domain`/`application`. The interface packages depend on the
core and wire concrete adapters at start-up.

Enforcement is twofold: `doculens-core` does not declare FastAPI as a dependency, so the API
framework cannot leak into it; and `packages/core/tests/unit/test_architecture.py` scans the
`domain` and `application` packages for imports of infrastructure libraries or of outer layers.

LangChain, when introduced, is confined to `doculens.infrastructure` (integration layer, §73);
no LangChain type crosses a port.

## Alternatives considered

- **One package containing all layers, worker as a sub-module.** Simpler, but nothing prevents
  `domain` from importing FastAPI beyond code review, and the worker image would carry the web
  framework.
- **Separate repositories per component.** Contradicts the monorepo layout in §71 and complicates
  atomic changes across the shared core.

## Consequences

- Three `pyproject.toml` files and a workspace root to maintain; one lockfile keeps them coherent.
- Adding an adapter means adding a dependency to `doculens-core`; both images install it. If this
  becomes a size concern, adapter extras (`doculens-core[chroma]`) can split them later.
- The planning note's original layout (worker depending on `apps/api`) is superseded by this ADR.
