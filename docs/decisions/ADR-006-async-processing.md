# ADR-006 — Async processing architecture

**Status:** Accepted (2026-09-26) · **Resolves provisionally:** OQ-13 (local queue), OQ-27
(enqueue consistency via ADR-011 option A) · **Touches:** OQ-2 (worker compute remains open;
the port is compute-agnostic) · **Refs:** §12, §47, §48, §49, §50, §51, §66, §67.

## Context

Document processing must not block API workers (§48, §65). The specification requires an
AWS-native mechanism with retry and dead-letter handling, idempotent jobs, and observability of
failed processing. Upload and reprocess create a document row in PostgreSQL and must start a
background job; the two writes cannot share a transaction (OQ-27 / ADR-011). Local development
needs a queue that does not require AWS (OQ-13). Worker hosting (Lambda vs ECS) is still open
(OQ-2).

## Decision

| Concern          | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Port             | `JobQueue` in `doculens.application.jobs`: `enqueue`, `claim` (visibility lease), `acknowledge`, `retry` (delayed), `dead_letter`, `list_dead_letters`. Payloads carry `document_id`, `job_id`, `attempt`, `request_id` — never file bytes.                                                                                                                                                                                                                                                                                                                                                        |
| Local backend    | Redis ZSET/HASH/LIST adapter (`QUEUE_BACKEND=redis`, OQ-13). `memory` backend for unit and integration tests.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Deployed backend | SQS adapter (`QUEUE_BACKEND=sqs`); required when `APP_ENV` is staging or production. Application retries bump `attempt` and requeue with delay; the SQS redrive policy remains the platform backstop.                                                                                                                                                                                                                                                                                                                                                                                              |
| Consistency      | Provisional ADR-011 option A: commit the document row, then enqueue. Enqueue failure is non-fatal for the request (`enqueue_quietly`); a later reconciliation sweep (same worker process or scheduled job) re-enqueues stale `UPLOADED` / restart rows.                                                                                                                                                                                                                                                                                                                                            |
| Worker           | `DocumentJobWorker` claims one job under a per-document in-process lock, runs `DocumentProcessor.process` under a hard timeout (< visibility lease), acknowledges on terminal outcomes (`PROCESSED`, `FAILED`, `NO_OP`, `CONCURRENT`, `SKIPPED`), retries on `DependencyUnavailableError`, timeouts and unexpected faults with bounded exponential backoff + jitter (floor after attempt 1), and on budget exhaustion marks the document `FAILED` then dead-letters. Reclaimed leases increment `attempt` so hung work cannot storm forever. Poison payloads are dead-lettered inside the adapter. |
| Idempotency      | Unchanged processor CAS and stage resume (ADR-015); duplicate jobs are safe.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Observability    | Structured logs on enqueue, retry, dead-letter, processed and interrupted (`operation=jobs.*`, `document_id`, `job_id`, `request_id`, `attempt`, `duration_ms`, `outcome`). Dead-letter depth is readable via `list_dead_letters` for alarms (§51).                                                                                                                                                                                                                                                                                                                                                |
| SQS retries      | Application retry uses `ChangeMessageVisibility` only (no delete+resend) so a crash cannot drop the sole copy; `ApproximateReceiveCount` is the attempt counter. Deployed environments require `QUEUE_SQS_DLQ_URL`.                                                                                                                                                                                                                                                                                                                                                                                |
| API / intake     | `DocumentIntakeService` and `DocumentService.reprocess` / `reindex` enqueue after a successful commit. The HTTP request never runs extraction, embedding or indexing.                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Entrypoint       | `doculens-worker` with no args consumes the queue; `process <id>` remains for operators.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

## Alternatives considered

- **In-process asyncio tasks from the API.** Violates §48 and fails when the API process dies
  mid-job; rejected.
- **Transactional outbox (ADR-011 B).** Stronger consistency; deferred until straggler latency
  from option A is proven insufficient.
- **LocalStack SQS for local.** Heavier than Redis; reserved if SQS fidelity is required in CI.

## Consequences

- New dependency `redis` in `doculens-core`; settings `QUEUE_*` and `REDIS_URL`.
- ADR-011 is accepted provisionally on option A; the reconciliation sweep is part of this
  architecture and may land as a worker subcommand in a follow-up.
- OQ-2 stays open: ECS vs Lambda does not change the `JobQueue` port.
- Upload HTTP transport is direct multipart (ADR-020); large-file / Lambda upgrade remains OQ-3.
