"""Background processing jobs (SPECIFICATIONS.md §48, §49, §66).

A job carries identifiers only: the worker loads the document and its original from storage.
Delivery is at-least-once, so every handler must be idempotent.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from doculens.domain.errors import DependencyUnavailableError, DomainError
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now


class QueueUnavailableError(DependencyUnavailableError):
    """The job queue cannot accept or deliver work right now (§67)."""

    code = "QUEUE_UNAVAILABLE"
    default_message = "The processing queue is temporarily unavailable."


class JobPermanentlyFailedError(DomainError):
    """The job exceeded its retry budget and was dead-lettered."""

    code = "JOB_PERMANENTLY_FAILED"
    default_message = "Document processing could not be completed."

    def __init__(self, message: str | None = None, *, diagnostics: str | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class PoisonJobError(DomainError):
    """A queue payload could not be decoded; it must be dead-lettered and never retried."""

    code = "POISON_JOB"
    default_message = "A processing job payload was unreadable."

    def __init__(
        self,
        message: str | None = None,
        *,
        receipt: str | None = None,
        diagnostics: str | None = None,
    ) -> None:
        super().__init__(message)
        self.receipt = receipt
        self.diagnostics = diagnostics


@dataclass(frozen=True, slots=True)
class ProcessingJob:
    """One unit of asynchronous document work (§48)."""

    document_id: UUID
    job_id: str
    attempt: int
    enqueued_at: datetime
    request_id: str | None = None

    @classmethod
    def create(
        cls,
        document_id: UUID,
        *,
        request_id: str | None = None,
        attempt: int = 1,
        now: datetime | None = None,
    ) -> "ProcessingJob":
        return cls(
            document_id=document_id,
            job_id=str(new_id()),
            attempt=max(1, attempt),
            enqueued_at=now if now is not None else utc_now(),
            request_id=request_id,
        )


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A job leased to one worker until acknowledged, retried or dead-lettered."""

    receipt: str
    job: ProcessingJob
    available_at: datetime


@dataclass(frozen=True, slots=True)
class DeadLetterRecord:
    """A job that exhausted retries; visible for operators and alarms (§51)."""

    job: ProcessingJob
    error: str
    dead_lettered_at: datetime


__all__ = [
    "ClaimedJob",
    "DeadLetterRecord",
    "JobPermanentlyFailedError",
    "PoisonJobError",
    "ProcessingJob",
    "QueueUnavailableError",
]
