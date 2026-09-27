# ADR-021 — Frontend foundation: session storage and unit tests

**Status:** Accepted (provisional)
**Date:** 2026-09-27
**Specification references:** §5.2, §5.4, §40, §53, §59

## Context

The web app needs an authenticated shell (login, registration, layout, dashboard placeholder)
before document and chat UI. `OQ-19` still leaves hosting mode and production token transport
open. `OQ-20` deferred a frontend unit runner until pure client logic existed. The API client,
session bootstrap and form validation are now non-trivial pure logic that need automated tests
without a full browser.

## Decision

1. **Central API client.** All HTTP calls go through `apps/web/src/lib/api/client.ts`. Feature
   modules (`auth.ts`, later documents/conversations) wrap that client. Components do not call
   `fetch` directly.
2. **Provisional token storage (OQ-19).** Access tokens live in memory only. Refresh tokens are
   kept in `sessionStorage` so a same-tab reload can resume the session. This is an interim
   choice until hosting decides httpOnly cookie refresh (preferred default in OQ-19). CSRF
   defenses for cookie transport remain deferred with that decision.
3. **Typed contracts.** `@doculens/shared-types` holds the auth and error envelopes used by the
   client; generation from OpenAPI remains planned.
4. **Unit tests (OQ-20).** `vitest` + Testing Library cover the API client, session helpers,
   credential schemas and auth forms. Playwright remains the E2E gate.

## Alternatives considered

- **localStorage for both tokens.** Rejected for access tokens (XSS blast radius) and deferred
  for refresh until OQ-19 closes on cookies.
- **httpOnly cookies immediately.** Rejected: requires CORS/cookie API support and a hosting
  decision (OQ-19); the API currently issues bearer tokens in JSON bodies only.
- **Playwright-only frontend tests.** Rejected once the client had refresh retry and validation
  logic that is cheaper to unit-test.

## Consequences

- Local and CI web jobs run `pnpm run test:web` in addition to lint, typecheck, build and e2e.
- Switching to cookie-based refresh later should not change the component API surface: only the
  session module and client refresh path move.
- Document/chat UI is intentionally out of scope for this ADR.
