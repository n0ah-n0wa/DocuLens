# ADR-013 — Resource authorization: structural ownership and not-found responses

**Status:** Accepted
**Date:** 2026-09-20
**Specification references:** §9, §16, §35, §53

## Context

§9 requires every user-owned resource to enforce `owner_id == authenticated_user.id` on the
server and forbids ID enumeration from exposing other users' resources. It does not say what a
request for someone else's resource should answer, and it does not say where the check lives.

## Decision

- **Ownership is structural, not a check.** Repository reads of user-owned resources require the
  owner's ID in their signature, use cases take the acting user's ID as their first argument, and
  the HTTP layer obtains that ID only from the verified bearer token. There is no code path that
  loads a resource by ID alone, so forgetting a check is not possible.
- **Foreign resources answer 404, with the same code and message as missing ones.** A user who
  guesses another user's identifier learns nothing, not even that the identifier exists. 403 is
  reserved for a caller who is known to the system but not allowed to act (a suspended account).
- **References across resources are checked the same way.** Moving a document or a conversation
  into a collection, or creating a conversation in one, verifies that the target collection is
  owned; otherwise the request answers `COLLECTION_NOT_FOUND` and nothing changes.
- **Every route outside `/health` and `/api/v1/auth` declares bearer security in OpenAPI.** A test
  fails when a new route is added without it.
- **Vectors and object keys** follow the same rule in their own stores (`user_id` filter, per-user
  key prefixes) when those adapters arrive (§11, §16).

## Alternatives considered

- **403 for foreign resources.** Conventional, but it confirms existence and turns the API into an
  oracle for enumerating identifiers; rejected by §9.
- **Authorization middleware or decorators.** Rejected: checks bolted onto routes can be omitted;
  putting the owner into repository signatures makes omission a type error.

## Consequences

- Repository methods for user-owned resources cannot be called without an owner; system-level
  operations (the processing worker) resolve the owner from the document first.
- Negative tests exist for every resource route with a second user and with no token, against
  the in-memory unit of work and against PostgreSQL.
