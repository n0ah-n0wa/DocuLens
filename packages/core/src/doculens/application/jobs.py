"""Job queue port and document-processing worker (SPECIFICATIONS.md §48, §49, §66, §67).

The API enqueues identifiers after the document row is committed (ADR-011). The worker claims a
job, runs :class:`~doculens.application.ingestion.DocumentProcessor`, then acknowledges, retries
with exponential backoff, or dead-letters. Processing state lives in PostgreSQL; the queue is
never authoritative (§47).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, cast

from doculens.domain.errors import DependencyUnavailableError
from doculens.domain.jobs import (
    ClaimedJob,
    DeadLetterRecord,
    PoisonJobError,
    ProcessingJob,
    QueueUnavailableError,
)
from doculens.domain.time import utc_now

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
StopPredicate = Callable[[], bool]

# Keep work inside the visibility lease so a timed-out run is not also reclaimed (§66).
_LEASE_SAFETY_RATIO = 0.8
_MIN_PROCESSING_TIMEOUT_SECONDS = 1.0


class _ProcessingReport(Protocol):
    @property
    def outcome(self) -> object: ...

    @property
    def status(self) -> object: ...

    @property
    def stages(self) -> tuple[object, ...]: ...


class _DocumentProcessor(Protocol):
    async def process(self, document_id: UUID) -> _ProcessingReport: ...

    async def fail_permanently(self, document_id: UUID, *, reason: str) -> _ProcessingReport: ...


class JobQueue(Protocol):
    """At-least-once job transport with visibility leases, delayed retry and a dead-letter lane."""

    async def enqueue(self, job: ProcessingJob) -> None:
        """Accept a job for delivery. Duplicate document ids are allowed (§49)."""
        ...

    async def claim(self, *, visibility_timeout_seconds: float) -> ClaimedJob | None:
        """Lease the next ready job, or ``None`` when the queue is empty.

        Poison payloads must be dead-lettered inside the adapter (or raise
        :class:`~doculens.domain.jobs.PoisonJobError`) so they never re-enter the ready lane.
        """
        ...

    async def acknowledge(self, claim: ClaimedJob) -> None:
        """Mark the lease complete; the job must not be delivered again."""
        ...

    async def retry(
        self, claim: ClaimedJob, *, delay_seconds: float, error: str
    ) -> ClaimedJob | None:
        """Return the job after ``delay_seconds`` with ``attempt + 1``, or ``None`` if gone."""
        ...

    async def dead_letter(self, claim: ClaimedJob, *, error: str) -> None:
        """Move the job out of the active queue for operator inspection (§51)."""
        ...

    async def list_dead_letters(self, *, limit: int = 100) -> list[DeadLetterRecord]:
        """Recent dead-lettered jobs (newest first); for tests and operator tooling."""
        ...


class DocumentJobDispatcher:
    """Enqueues document processing after a successful PostgreSQL commit (ADR-011)."""

    def __init__(self, *, queue: JobQueue, clock: Clock = utc_now) -> None:
        self._queue = queue
        self._clock = clock

    async def enqueue(
        self, document_id: UUID, *, request_id: str | None = None, attempt: int = 1
    ) -> ProcessingJob:
        job = ProcessingJob.create(
            document_id, request_id=request_id, attempt=attempt, now=self._clock()
        )
        await self._queue.enqueue(job)
        logger.info(
            "document processing enqueued",
            extra={
                "operation": "jobs.enqueue",
                "document_id": str(document_id),
                "job_id": job.job_id,
                "request_id": request_id,
                "attempt": job.attempt,
            },
        )
        return job

    async def enqueue_quietly(
        self, document_id: UUID, *, request_id: str | None = None
    ) -> ProcessingJob | None:
        """Enqueue without failing the caller when the queue is down (ADR-011)."""
        try:
            return await self.enqueue(document_id, request_id=request_id)
        except QueueUnavailableError as exc:
            logger.warning(
                "document processing enqueue deferred",
                extra={
                    "operation": "jobs.enqueue_deferred",
                    "document_id": str(document_id),
                    "request_id": request_id,
                    "error_code": exc.code,
                },
            )
            return None


class DocumentJobWorker:
    """Claims jobs, runs the processor, and applies retry / dead-letter policy (§48, §66)."""

    def __init__(  # noqa: PLR0913 - every bound the worker needs, wired by the composition root
        self,
        *,
        queue: JobQueue,
        processor: _DocumentProcessor | Callable[[], _DocumentProcessor],
        max_attempts: int = 5,
        backoff_base_seconds: float = 2.0,
        backoff_max_seconds: float = 300.0,
        visibility_timeout_seconds: float = 300.0,
        processing_timeout_seconds: float | None = None,
        poll_interval_seconds: float = 1.0,
        clock: Clock = utc_now,
    ) -> None:
        if max_attempts < 1:
            message = "max_attempts must be at least 1"
            raise ValueError(message)
        self._queue = queue
        self._processor = processor
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds
        self._visibility = visibility_timeout_seconds
        self._processing_timeout = (
            processing_timeout_seconds
            if processing_timeout_seconds is not None
            else max(
                _MIN_PROCESSING_TIMEOUT_SECONDS,
                visibility_timeout_seconds * _LEASE_SAFETY_RATIO,
            )
        )
        if self._processing_timeout >= self._visibility:
            message = (
                "processing_timeout_seconds must be strictly less than visibility_timeout_seconds"
            )
            raise ValueError(message)
        self._poll_interval = poll_interval_seconds
        self._clock = clock
        # One in-process lock per document prevents duplicate concurrent work in this worker (§49).
        self._document_locks: dict[UUID, asyncio.Lock] = {}

    def _resolve_processor(self) -> _DocumentProcessor:
        candidate = self._processor
        if callable(candidate) and not hasattr(candidate, "process"):
            return candidate()
        return cast("_DocumentProcessor", candidate)

    def _lock_for(self, document_id: UUID) -> asyncio.Lock:
        lock = self._document_locks.get(document_id)
        if lock is None:
            lock = asyncio.Lock()
            self._document_locks[document_id] = lock
        return lock

    async def run_once(self) -> bool:
        """Process at most one job. Returns ``True`` when a job was claimed."""
        try:
            claim = await self._queue.claim(visibility_timeout_seconds=self._visibility)
        except PoisonJobError as exc:
            logger.exception(
                "poison job discarded",
                extra={
                    "operation": "jobs.poison",
                    "receipt": exc.receipt,
                    "error_code": exc.code,
                    "diagnostics": exc.diagnostics,
                },
            )
            return True
        except QueueUnavailableError as exc:
            logger.warning(
                "job queue unavailable while claiming",
                extra={"operation": "jobs.claim_unavailable", "error_code": exc.code},
            )
            return False
        if claim is None:
            return False
        await self._handle(claim)
        return True

    async def run_forever(
        self,
        *,
        stop_when: StopPredicate | None = None,
        idle: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Claim and handle jobs until ``stop_when`` returns true."""
        while stop_when is None or not stop_when():
            worked = await self.run_once()
            if worked:
                continue
            if idle is not None:
                await idle()
            else:
                await asyncio.sleep(self._poll_interval)

    async def _handle(self, claim: ClaimedJob) -> None:
        job = claim.job
        started = self._clock()
        lock = self._lock_for(job.document_id)
        if lock.locked():
            # Another coroutine in this process already owns the document; delay and retry later
            # so we do not amplify duplicate deliveries into a retry storm.
            await self._safe_retry(
                claim,
                delay_seconds=self.backoff_seconds(job.attempt),
                error="DOCUMENT_BUSY: another worker holds this document",
            )
            self._log_outcome(job, started, outcome="deferred_busy", error_code="DOCUMENT_BUSY")
            return

        async with lock:
            try:
                report = await asyncio.wait_for(
                    self._resolve_processor().process(job.document_id),
                    timeout=self._processing_timeout,
                )
            except TimeoutError:
                await self._retry_or_dead_letter(
                    claim,
                    error=(
                        f"PROCESSING_TIMEOUT: exceeded {self._processing_timeout:.1f}s "
                        f"(visibility {self._visibility:.1f}s)"
                    ),
                )
                self._log_outcome(
                    job, started, outcome="processing_timeout", error_code="PROCESSING_TIMEOUT"
                )
                return
            except DependencyUnavailableError as exc:
                await self._retry_or_dead_letter(claim, error=f"{exc.code}: {exc.message}")
                self._log_outcome(job, started, outcome="retryable_dependency", error_code=exc.code)
                return
            except Exception as exc:  # noqa: BLE001 - unexpected faults still follow the retry budget
                await self._retry_or_dead_letter(claim, error=f"{type(exc).__name__}: {exc}")
                self._log_outcome(
                    job, started, outcome="retryable_error", error_code=type(exc).__name__
                )
                return

            await self._safe_acknowledge(claim)
            self._log_report(job, started, report)

    async def _retry_or_dead_letter(self, claim: ClaimedJob, *, error: str) -> None:
        if claim.job.attempt >= self._max_attempts:
            await self._abandon_document(claim.job.document_id, reason=error)
            await self._safe_dead_letter(claim, error=error)
            logger.error(
                "document processing dead-lettered",
                extra={
                    "operation": "jobs.dead_letter",
                    "document_id": str(claim.job.document_id),
                    "job_id": claim.job.job_id,
                    "request_id": claim.job.request_id,
                    "attempt": claim.job.attempt,
                    "error": error,
                },
            )
            return
        delay = self.backoff_seconds(claim.job.attempt)
        await self._safe_retry(claim, delay_seconds=delay, error=error)
        logger.warning(
            "document processing will retry",
            extra={
                "operation": "jobs.retry",
                "document_id": str(claim.job.document_id),
                "job_id": claim.job.job_id,
                "request_id": claim.job.request_id,
                "attempt": claim.job.attempt,
                "next_attempt": claim.job.attempt + 1,
                "delay_seconds": round(delay, 3),
                "error": error,
            },
        )

    async def _abandon_document(self, document_id: UUID, *, reason: str) -> None:
        try:
            await self._resolve_processor().fail_permanently(document_id, reason=reason)
        except Exception:
            logger.exception(
                "failed to mark document FAILED after retry exhaustion",
                extra={
                    "operation": "jobs.abandon_failed",
                    "document_id": str(document_id),
                },
            )

    async def _safe_acknowledge(self, claim: ClaimedJob) -> None:
        try:
            await self._queue.acknowledge(claim)
        except QueueUnavailableError as exc:
            logger.warning(
                "acknowledge deferred; delivery may repeat",
                extra={
                    "operation": "jobs.ack_unavailable",
                    "document_id": str(claim.job.document_id),
                    "job_id": claim.job.job_id,
                    "error_code": exc.code,
                },
            )

    async def _safe_retry(self, claim: ClaimedJob, *, delay_seconds: float, error: str) -> None:
        try:
            await self._queue.retry(claim, delay_seconds=delay_seconds, error=error)
        except QueueUnavailableError as exc:
            logger.warning(
                "retry deferred; visibility lease will expire",
                extra={
                    "operation": "jobs.retry_unavailable",
                    "document_id": str(claim.job.document_id),
                    "job_id": claim.job.job_id,
                    "error_code": exc.code,
                },
            )

    async def _safe_dead_letter(self, claim: ClaimedJob, *, error: str) -> None:
        try:
            await self._queue.dead_letter(claim, error=error)
        except QueueUnavailableError as exc:
            logger.exception(
                "dead-letter failed; message may redeliver",
                extra={
                    "operation": "jobs.dead_letter_unavailable",
                    "document_id": str(claim.job.document_id),
                    "job_id": claim.job.job_id,
                    "error_code": exc.code,
                },
            )

    def backoff_seconds(self, attempt: int) -> float:
        """Bounded exponential backoff with full jitter (§66).

        Attempt 1 may retry immediately (jitter over a small base). Later attempts keep a floor of
        half the exponential ceiling so a thundering herd cannot collapse to zero delay.
        """
        exp = self._backoff_base * (2 ** max(0, attempt - 1))
        ceiling = min(self._backoff_max, exp)
        if ceiling <= 0:
            return 0.0
        delay = float(random.uniform(0, ceiling))  # noqa: S311 - jitter, not cryptography
        if attempt <= 1:
            return delay
        return float(max(delay, ceiling * 0.5))

    def _log_report(self, job: ProcessingJob, started: datetime, report: _ProcessingReport) -> None:
        duration_ms = (self._clock() - started).total_seconds() * 1000
        outcome = getattr(report.outcome, "value", str(report.outcome))
        status = report.status
        status_value = status.value if status is not None and hasattr(status, "value") else None
        stages = [getattr(stage, "value", str(stage)) for stage in report.stages]
        logger.info(
            "document processing finished",
            extra={
                "operation": "jobs.processed",
                "document_id": str(job.document_id),
                "job_id": job.job_id,
                "request_id": job.request_id,
                "attempt": job.attempt,
                "outcome": outcome,
                "status": status_value,
                "stages": stages,
                "duration_ms": round(duration_ms, 1),
                "status_ok": outcome in {"processed", "no_op", "concurrent", "skipped", "failed"},
            },
        )

    def _log_outcome(
        self,
        job: ProcessingJob,
        started: datetime,
        *,
        outcome: str,
        error_code: str,
    ) -> None:
        duration_ms = (self._clock() - started).total_seconds() * 1000
        logger.warning(
            "document processing interrupted",
            extra={
                "operation": "jobs.interrupted",
                "document_id": str(job.document_id),
                "job_id": job.job_id,
                "request_id": job.request_id,
                "attempt": job.attempt,
                "outcome": outcome,
                "error_code": error_code,
                "duration_ms": round(duration_ms, 1),
            },
        )


def with_next_attempt(job: ProcessingJob, *, now: datetime) -> ProcessingJob:
    """A new payload for a delayed retry or a reclaimed lease (counts toward the budget)."""
    return replace(job, attempt=job.attempt + 1, enqueued_at=now)


__all__ = [
    "DocumentJobDispatcher",
    "DocumentJobWorker",
    "JobQueue",
    "StopPredicate",
    "with_next_attempt",
]
