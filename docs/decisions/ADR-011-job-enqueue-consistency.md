# ADR-011 — Consistency between the document row and the processing job

**Status:** Proposed (blocks `OQ-27`; not binding until accepted)
**Date:** 2026-09-19
**Specification references:** §12, §46, §48, §49, §66, §67

## Context

Uploading a document produces two writes in different systems: the document row in PostgreSQL and
the processing job in the queue. The queue does not participate in the database transaction (§46).
Whichever order is chosen, one failure mode exists:

- **Commit, then enqueue.** If the enqueue fails (queue outage, Lambda timeout after the commit),
  the document sits in `UPLOADED` forever with no job.
- **Enqueue, then commit.** If the commit fails, the worker receives a job for a document that does
  not exist.

The specification requires idempotent ingestion (§49), tolerance of transient AWS failures (§66),
observable and recoverable partial failures (§31) and controlled behaviour when a dependency is
down (§67). It does not say how the two writes are reconciled. A decision is needed before the
upload use case is implemented.

## Options

### A. Commit first, enqueue second, reconcile stragglers

The API commits the row, then enqueues. A periodic reconciliation (scheduled Lambda or worker
sweep) enqueues a job for every document still in `UPLOADED` older than a threshold. The worker
is idempotent, so a duplicate enqueue is harmless.

- Simple; no schema beyond a `created_at` index; the common path has no extra latency.
- Recovery latency equals the sweep interval; requires a scheduled component.

### B. Transactional outbox

The API writes the row and an `outbox` row in one transaction. A relay (scheduled Lambda or a
database trigger/stream consumer) publishes outbox rows to the queue and marks them sent.

- Exactly-once intent capture; no straggler can exist.
- More moving parts (outbox table, relay, its own retries and monitoring); every job is delayed by
  the relay interval.

### C. Enqueue first with a tolerant worker

The API enqueues, then commits. The worker treats a missing row as "not yet visible" and retries
with backoff before dead-lettering.

- No extra component.
- Relies on retry timing to cover commit latency; a permanently failed commit produces a
  dead-letter for a document that never existed, which pollutes failure metrics.

## Recommendation

Option A. It satisfies every cited requirement with the least machinery, matches the worker's
idempotency guarantee that §49 already demands, and the reconciliation sweep doubles as the
recovery mechanism for interrupted deletions (`DELETING` stragglers, §31). Option B remains the
upgrade path if straggler latency ever matters.

## Consequences (if accepted)

- The upload use case commits before enqueueing and treats an enqueue failure as non-fatal for the
  request: the document is created, and processing starts on the next sweep.
- A scheduled reconciliation component becomes part of ADR-006 (async processing) and the compute
  module in Terraform.
- `UPLOADED` age becomes a metric with an alarm (§51).
- `OQ-27` is closed in `docs/planning/open-questions.md` and `docs/architecture.md` §8 moves the
  item from _Pending_ to _Planned_.
