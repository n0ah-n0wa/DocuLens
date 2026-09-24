# ADR-019 — Document lifecycle: search, deletion, reprocess and re-index

**Status:** Accepted (2026-09-24) · **Resolves provisionally:** OQ-5 (reprocess vs re-index),
OQ-7 (tombstone vs hard delete) · **Touches:** OQ-10 (metadata search half) ·
**Refs:** §7.3, §7.8, §9, §29, §31, §32, §33, §46, §49, §67, §69.

## Context

§29 requires list, search, inspect, rename, move, delete, reprocess and re-index. List, inspect,
rename and move already existed. Search was ambiguous (OQ-10). Delete must purge PostgreSQL
content, the object store, vectors, pages, chunks and citations "where appropriate" (§31), be
idempotent, and survive partial failures of stores that cannot share a transaction (§46).
Reprocess and re-index are two verbs with one endpoint in §33 (OQ-5). The state machine already
has `DELETING` / `DELETED`, which contradicts a literal hard delete of the row.

## Decision

| Concern                      | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Metadata search (OQ-10)      | `GET /api/v1/documents?q=` is a case-insensitive literal substring of the filename, optionally restricted to a collection. `LIKE` wildcards are escaped. Semantic search (`POST /search`) remains a later feature.                                                                                                                                                                                                                                                                                  |
| Reprocess vs re-index (OQ-5) | `POST /documents/{id}/reprocess` restarts from `VALIDATING` (full pipeline from the stored original). `POST /documents/{id}/reindex` restarts from `CHUNKING` (re-chunk and re-embed from stored pages). Both are compare-and-set, both replace vectors by deterministic id. Re-index without pages is `DOCUMENT_CANNOT_REINDEX`. An `UPLOADED` reprocess is a no-op. A document already in the target state is returned unchanged. The worker command still runs the stages; the queue is ADR-006. |
| Who may restart              | `READY` and `FAILED` may reprocess or re-index. Mid-pipeline states refuse a restart (`INVALID_STATUS_TRANSITION`). `DELETING` refuses with `DOCUMENT_DELETION_IN_PROGRESS`. `DELETED` is not found.                                                                                                                                                                                                                                                                                                |
| Tombstone (OQ-7)             | Soft delete: the row stays with status `DELETED`. Pages, chunks, vectors and the S3 object are purged. Citations keep `document_id` and `quoted_text` and lose `chunk_id` (`ON DELETE SET NULL`). A cited document is never hard-deleted (`ON DELETE RESTRICT`). Tombstones are invisible to list, search, get, update, reprocess and re-index (404, ADR-013). A second delete is a no-op. Quota and duplicate checks already ignore `DELETED` rows.                                                |
| Deletion saga                | `DELETING` → vectors (`VectorStore.delete_document`) → object (`ObjectStorage.delete`) → pages and chunks → `DELETED`. Every step is idempotent. An external failure leaves the row `DELETING` and raises a retryable 503 (`VECTOR_STORE_UNAVAILABLE` / `STORAGE_UNAVAILABLE`). A concurrent compare-and-set that finds the row already `DELETED` succeeds. Any non-terminal state may move to `DELETING`, so a document can be cancelled while it is still processing.                             |
| Reads of a tombstone         | `GET` / `PATCH` / reprocess / re-index answer `DOCUMENT_NOT_FOUND`, the same as an unknown or foreign id. The owner who just deleted sees the document vanish from listings.                                                                                                                                                                                                                                                                                                                        |

## Alternatives considered

- **Hard-delete the row and `ON DELETE SET NULL` the citation's `document_id`.** Loses the
  filename and makes "source deleted" harder to render; refused by the existing `RESTRICT` FK
  until a migration. The state machine already named `DELETED`.
- **Cascade citations on document delete.** Breaks message immutability (§28).
- **One `/reprocess` endpoint with a `mode` body.** Two URLs match the two verbs in §29 and keep
  the OpenAPI operation distinct.
- **Run the pipeline inside the API request.** Contradicts §6 / §65; the worker already resumes
  from the current stage.

## Consequences

- `SPECIFICATIONS.md` §31 is amended: the document row is retained as a `DELETED` tombstone;
  content is purged. §33 gains `POST /documents/{id}/reindex` and `GET /documents?q=`.
- A periodic purge of old tombstones is still open under §69.
- Collection-level `?delete_documents=true` (OQ-9) can now call this saga; it is not added here.
- Upload (`OQ-3`) and job enqueue (ADR-011) remain the missing pieces of the end-to-end flow.
