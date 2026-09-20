# ADR-007 — Authentication strategy

**Status:** Accepted (token storage in the browser remains `OQ-19`)
**Date:** 2026-09-20
**Specification references:** §5.1, §8, §9, §37, §47, §53, §54, §68

## Context

§8 requires registration, login, logout, short-lived access tokens, longer-lived revocable refresh
tokens with rotation, Argon2id (or equivalent) password hashing, password validation and account
status checks, with JWT claims for issuer, audience, subject, issued-at, expiration and a token
identifier. §5.1 names `python-jose` "or equivalent". §47 forbids Redis as the source of truth
for persistent state, so revocation must live in PostgreSQL. `OQ-12` asked which JWT library to
use and whether access tokens need a denylist on logout; `OQ-19` asks where the browser stores
tokens, which the API contract must not pre-empt.

## Decision

| Concern            | Decision                                                                                                                                                                                                         |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Password hashing   | Argon2id via `argon2-cffi` with the library defaults (t=3, m=64 MiB, p=4); rehashed on login when parameters change; hashing runs in a worker thread                                                             |
| Password policy    | 12 to 128 characters (minimum configurable), not equal to the email; no composition rules                                                                                                                        |
| Email              | Normalised to lower-case, format-checked; the database enforces lower-case storage                                                                                                                               |
| JWT library        | PyJWT (`python-jose` has had slower maintenance and CVEs; PyJWT is the actively maintained equivalent)                                                                                                           |
| Signing            | HS256 with a per-environment secret of at least 32 characters from configuration                                                                                                                                 |
| Claims             | `iss`, `aud`, `sub` (user ID), `jti`, `iat`, `exp`, `typ` (`access` or `refresh`), `fam` (refresh only); all required and verified on decode                                                                     |
| Access tokens      | 15 minutes by default (bounded 1 to 60 minutes); not stored and not denylisted                                                                                                                                   |
| Refresh tokens     | 30 days by default; one PostgreSQL row per token keyed by `jti`, grouped into a family per login; the family has an absolute lifetime fixed at login that rotation never extends                                 |
| Rotation and reuse | Every refresh issues a new token and marks the old one as replaced through an atomic compare-and-set; a presentation that loses the race, or of an already replaced or revoked token, revokes the whole family   |
| Logout             | Revokes the family of the presented refresh token; idempotent                                                                                                                                                    |
| Account status     | `SUSPENDED` answers 403 `ACCOUNT_SUSPENDED` everywhere; `DELETED` behaves as unknown credentials                                                                                                                 |
| Error contract     | 401 with `WWW-Authenticate: Bearer` and stable codes (`UNAUTHENTICATED`, `INVALID_CREDENTIALS`, `INVALID_TOKEN`, `TOKEN_EXPIRED`); unknown email and wrong password are indistinguishable and take the same time |
| Transport          | Tokens are returned in the JSON response body; the access token is sent as a bearer header                                                                                                                       |
| Brute force        | Every auth endpoint is limited per client address, and login also per account (keyed by a hash of the email); over budget answers 429 `RATE_LIMITED` with `Retry-After`; concurrent Argon2 work is bounded       |
| Audit events       | Registration, login, failed login (with a reason class), token reuse and logout are logged with user and family identifiers only; never an email, password or token                                              |

## Alternatives considered

- **Access-token denylist in Redis on logout.** Rejected for now: access tokens live 15 minutes and
  §8 requires revocation of refresh tokens only. A denylist can be added later without changing
  the contract if a shorter revocation window becomes a requirement.
- **Opaque random refresh tokens (hashed at rest).** Equivalent security; JWTs were chosen because
  §8 describes both token kinds as JWTs and one codec serves both.
- **Cookie-based transport decided now.** Rejected: it is `OQ-19` and depends on the hosting
  decision. Returning tokens in the body works with an in-memory access token plus an httpOnly
  refresh cookie set by a BFF layer, or with a cookie-based API variant added later.
- **RS256 / key rotation.** Not needed while one service issues and verifies tokens; revisit if a
  second consumer appears.

## Consequences

- New `refresh_tokens` table (migration `0002`), cascading with its user.
- The rate limiter behind the auth endpoints is an in-process fixed-window store, exact per
  instance; the Redis adapter (§37, §47) replaces the store behind the same port so limits become
  fleet-wide. Client addresses are taken from the connection until `OQ-19` fixes the proxy whose
  forwarding headers may be trusted.
- Registration answers 409 for an existing email, as §33 requires. That reveals whether an
  address has an account; the per-address rate limit bounds how fast it can be probed, and the
  alternative (a neutral response plus email verification) depends on `OQ-24`.
- Email verification, password reset and account deletion remain `OQ-24`.
- The web client must send `Authorization: Bearer <access_token>` and refresh before expiry.
