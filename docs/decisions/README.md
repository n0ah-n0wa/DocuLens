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

| ADR                                               | Title                                     | Status   | Blocked by                      |
| ------------------------------------------------- | ----------------------------------------- | -------- | ------------------------------- |
| [ADR-001](ADR-001-backend-architecture.md)        | Backend architecture                      | Accepted | —                               |
| ADR-002                                           | Vector database selection                 | Pending  | OQ-1                            |
| ADR-003                                           | Chunking strategy                         | Pending  | OQ-16                           |
| ADR-004                                           | Embedding provider                        | Pending  | OQ-17                           |
| ADR-005                                           | LLM provider                              | Pending  | OQ-17                           |
| ADR-006                                           | Async processing architecture             | Pending  | OQ-2                            |
| ADR-007                                           | Authentication strategy                   | Pending  | OQ-12, OQ-19                    |
| ADR-008                                           | AWS deployment architecture               | Pending  | OQ-1, OQ-3, OQ-3b, OQ-19, OQ-23 |
| ADR-009                                           | RAG evaluation strategy                   | Pending  | OQ-22                           |
| [ADR-010](ADR-010-repository-tooling.md)          | Repository tooling                        | Accepted | —                               |
| [ADR-011](ADR-011-job-enqueue-consistency.md)     | Document row / job enqueue consistency    | Proposed | OQ-27 (decision requested)      |
| [ADR-012](ADR-012-lambda-database-connections.md) | Database connection management for Lambda | Proposed | OQ-28 (decision requested)      |

Decisions that only affect ingestion behaviour (OQ-3, OQ-5, OQ-6, OQ-7, OQ-14), the conversation
model (OQ-8, OQ-18), the RAG mechanisms (OQ-29, OQ-30) or observability (OQ-21) are recorded as
new ADRs (013 onwards) when taken.
