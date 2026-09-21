# Architecture decisions

Architecture Decision Records (ADRs) capture the decisions that shape DocuLens (SPECIFICATIONS.md
§78, §91). Each record states the context, the decision, the alternatives and the consequences, so
that later readers understand why the system looks the way it does.

## Rules

- A decision that changes architecture, replaces a technology named in `SPECIFICATIONS.md`, or
  resolves an ambiguity in it **must** have an ADR. The specification is updated in the same change
  when the decision contradicts it; requirements are never changed silently.
- Open ambiguities live in [`../planning/open-questions.md`](../planning/open-questions.md) until
  decided. Resolving one means: write the ADR, fill in that file's decision log, and update
  [`../architecture.md`](../architecture.md) from _Pending_ to _Planned_ or _Implemented_.
- Numbers 001–009 are reserved for the topics the specification names; later numbers are free.
- Copy [`ADR-000-template.md`](ADR-000-template.md), take the next number, and add a row below.

## Status legend

| Status     | Meaning                                                        |
| ---------- | -------------------------------------------------------------- |
| Accepted   | In force; the code follows it                                  |
| Proposed   | Written, under review, not yet binding                         |
| Pending    | Not yet written; the open question(s) that block it are listed |
| Superseded | Replaced by a later ADR named in the record                    |

## Index

| ADR                                               | Title                                          | Status                 | Blocked by                                       |
| ------------------------------------------------- | ---------------------------------------------- | ---------------------- | ------------------------------------------------ |
| [ADR-001](ADR-001-backend-architecture.md)        | Backend architecture                           | Accepted               | —                                                |
| [ADR-002](ADR-002-vector-database.md)             | Vector database and the vector-store adapter   | Accepted (adapter)     | OQ-1: hosting when deployed still open           |
| [ADR-003](ADR-003-chunking-strategy.md)           | Chunking strategy                              | Accepted               | OQ-16 provisional                                |
| [ADR-004](ADR-004-embedding-provider.md)          | Embedding provider                             | Accepted (provisional) | OQ-17: port and first adapter fixed, vendor open |
| ADR-005                                           | LLM provider                                   | Pending                | OQ-17                                            |
| ADR-006                                           | Async processing architecture                  | Pending                | OQ-2                                             |
| [ADR-007](ADR-007-authentication-strategy.md)     | Authentication strategy                        | Accepted               | OQ-19 (token storage) still open                 |
| ADR-008                                           | AWS deployment architecture                    | Pending                | OQ-1, OQ-3, OQ-3b, OQ-19, OQ-23                  |
| ADR-009                                           | RAG evaluation strategy                        | Pending                | OQ-22                                            |
| [ADR-010](ADR-010-repository-tooling.md)          | Repository tooling                             | Accepted               | —                                                |
| [ADR-011](ADR-011-job-enqueue-consistency.md)     | Document row / job enqueue consistency         | Proposed               | OQ-27 (decision requested)                       |
| [ADR-012](ADR-012-lambda-database-connections.md) | Database connection management for Lambda      | Proposed               | OQ-28 (decision requested)                       |
| [ADR-013](ADR-013-authorization-responses.md)     | Resource authorization and not-found responses | Accepted               | —                                                |
| [ADR-014](ADR-014-object-storage.md)              | Object storage adapters                        | Accepted               | OQ-13 resolved; OQ-3 still open                  |
| [ADR-015](ADR-015-ingestion-pipeline.md)          | PDF ingestion pipeline                         | Accepted               | OQ-6, OQ-14 provisional; OQ-3 open               |
| [ADR-016](ADR-016-retrieval-service.md)           | Retrieval, reranking and the RAG boundary      | Accepted               | OQ-4 provisional; OQ-8, OQ-17 open; OQ-29 partly |

Decisions that only affect ingestion behaviour (OQ-3, OQ-5, OQ-6, OQ-7, OQ-14), the conversation
model (OQ-8, OQ-18), the remaining RAG mechanisms (OQ-30) or observability (OQ-21) are
recorded as new ADRs (013 onwards) when taken.
