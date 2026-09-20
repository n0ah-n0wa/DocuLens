# ADR-002 — Vector database and the vector-store adapter

**Status:** Accepted for the adapter (2026-09-20); hosting when deployed remains **OQ-1** ·
**Refs:** §5.3, §16, §18, §31, §32, §53, §67, §72.

## Context

ChromaDB is the required vector store (§5.3, §16). Every vector must carry `user_id`,
`document_id`, `collection_id`, `page_number`, `chunk_id` and `chunk_index`, queries must enforce
ownership, and a user must never retrieve another user's vectors (§16). Re-indexing must not
create duplicates (§32); deletion must remove a document's vectors (§31). The store must sit
behind an abstraction so that the RAG logic does not depend on it (§18, §72). Where a ChromaDB
server runs in the Lambda-based AWS design is still open (OQ-1).

## Decision

| Concern               | Decision                                                                                                                                                                                                                                                                                                                                                                                                  |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Port                  | `doculens.application.vectors.VectorStore`: `upsert`, `replace_document`, `delete`, `delete_document`, `search`, `count`, `ensure_collection`, `drop_collection`; every operation takes the owner, so a cross-user operation cannot be expressed                                                                                                                                                          |
| Vector identity       | `vector_id == str(chunk_id)`; chunk ids are stable per document and index, so re-indexing upserts in place and never duplicates (§32)                                                                                                                                                                                                                                                                     |
| Metadata              | `VectorMetadata`: the six §16 fields plus `embedding_model`, `embedding_provider` and `content_hash` for staleness detection; serialised as flat scalars, absent values omitted rather than null; the chunk text is stored as the vector's document for context assembly                                                                                                                                  |
| Tenant isolation      | Enforced in the adapter: every `where` clause starts with `user_id`, every delete is scoped by `user_id`, results are re-checked against the filter before they are returned, and an upsert first verifies that none of its ids belongs to another user (`VectorOwnershipConflictError`)                                                                                                                  |
| Re-indexing           | `describe_document` reports the stored vectors' metadata so callers embed only missing or changed chunks; `prune_document` removes the document's ids outside the kept set; `replace_document` combines upsert and prune; `delete_document` removes every vector of the owner's document; `set_document_collection` rewrites the vectors after a move so `collection_id` stays true (§29); all idempotent |
| Collections           | One Chroma collection per embedding model, named `<CHROMA_COLLECTION_PREFIX>-<model slug>`, created on first use with cosine space and the model recorded in its metadata; a model change lands in a new collection instead of mixing dimensions; `drop_collection` supports rebuilds and tests                                                                                                           |
| Scores                | Cosine similarity (`1 - distance`), higher is closer; `VECTOR_SEARCH_MAX_RESULTS` caps any query                                                                                                                                                                                                                                                                                                          |
| Timeouts and rebuilds | Every call is bounded by `CHROMA_TIMEOUT_SECONDS`; a collection that disappears (rebuild, §32) invalidates the cached handle and is reported as retryable so the next run recreates it                                                                                                                                                                                                                    |
| Errors                | `VectorStoreUnavailableError` (a `DependencyUnavailableError`: connection, timeout, server errors, so processing stays retryable, §67), `VectorDimensionMismatchError`, `VectorOwnershipConflictError`, `VectorStoreError`; provider messages are bounded diagnostics, never user messages                                                                                                                |
| Client                | `chromadb-client` (the thin HTTP client, pinned to the server version) through its async API; no embedding function is configured on the collection, vectors always come from the `EmbeddingProvider`                                                                                                                                                                                                     |
| Fake                  | `InMemoryVectorStore` with the same ownership rules, run against the same contract suite; `VECTOR_STORE=memory` is refused when deployed                                                                                                                                                                                                                                                                  |

## Alternatives considered

- **Enforcing ownership in callers only.** One missed filter would leak vectors; the adapter is
  the single place that cannot be bypassed.
- **One collection for all models.** Chroma fixes a collection's dimension at first insert; a
  model change would fail or silently mix spaces.
- **Content-hash vector ids.** Would duplicate vectors on every re-chunk; chunk-derived ids keep
  citations and vectors stable when content is unchanged.
- **pgvector.** Would remove a service but contradicts §5.3; the port keeps it possible.

## Consequences

- New runtime dependency `chromadb-client` in `doculens-core`.
- The `EMBEDDING` and `INDEXING` pipeline stages and retrieval (§17–§18) build on this port next.
- Hosting (OQ-1) decides the server's network position and authentication (`CHROMA_API_TOKEN`);
  deployed environments require `https` and the `chroma` store.
