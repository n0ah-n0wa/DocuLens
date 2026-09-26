"""In-process job queue for tests and single-process local runs (SPECIFICATIONS.md §48)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from doculens.application.jobs import with_next_attempt
from doculens.domain.jobs import ClaimedJob, DeadLetterRecord, ProcessingJob
from doculens.domain.time import utc_now


@dataclass
class _Pending:
    job: ProcessingJob
    available_at: datetime


@dataclass
class _Lease:
    claim: ClaimedJob
    expires_at: datetime


@dataclass
class InMemoryJobQueue:
    """Ready queue + delayed retries + visibility leases + dead-letter list."""

    clock: Callable[[], datetime] = utc_now
    _ready: list[_Pending] = field(default_factory=list)
    _leases: dict[str, _Lease] = field(default_factory=dict)
    _dead: list[DeadLetterRecord] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def enqueue(self, job: ProcessingJob) -> None:
        async with self._lock:
            self._ready.append(_Pending(job=job, available_at=job.enqueued_at))

    async def claim(self, *, visibility_timeout_seconds: float) -> ClaimedJob | None:
        async with self._lock:
            now = self.clock()
            self._reclaim_expired(now)
            for index, pending in enumerate(self._ready):
                if pending.available_at > now:
                    continue
                self._ready.pop(index)
                receipt = str(uuid4())
                claim = ClaimedJob(
                    receipt=receipt, job=pending.job, available_at=pending.available_at
                )
                self._leases[receipt] = _Lease(
                    claim=claim,
                    expires_at=now + timedelta(seconds=visibility_timeout_seconds),
                )
                return claim
            return None

    async def acknowledge(self, claim: ClaimedJob) -> None:
        async with self._lock:
            self._leases.pop(claim.receipt, None)

    async def retry(
        self, claim: ClaimedJob, *, delay_seconds: float, error: str
    ) -> ClaimedJob | None:
        del error
        async with self._lock:
            if self._leases.pop(claim.receipt, None) is None:
                return None
            now = self.clock()
            nxt = with_next_attempt(claim.job, now=now)
            self._ready.append(
                _Pending(job=nxt, available_at=now + timedelta(seconds=max(0.0, delay_seconds)))
            )
            return ClaimedJob(receipt=claim.receipt, job=nxt, available_at=now)

    async def dead_letter(self, claim: ClaimedJob, *, error: str) -> None:
        async with self._lock:
            self._leases.pop(claim.receipt, None)
            self._dead.append(
                DeadLetterRecord(job=claim.job, error=error, dead_lettered_at=self.clock())
            )

    async def list_dead_letters(self, *, limit: int = 100) -> list[DeadLetterRecord]:
        async with self._lock:
            return list(reversed(self._dead[-limit:]))

    def _reclaim_expired(self, now: datetime) -> None:
        expired = [receipt for receipt, lease in self._leases.items() if lease.expires_at <= now]
        for receipt in expired:
            lease = self._leases.pop(receipt)
            # Count reclaim toward the retry budget so a hung worker cannot storm forever (§66).
            bumped = with_next_attempt(lease.claim.job, now=now)
            self._ready.append(_Pending(job=bumped, available_at=now))


__all__ = ["InMemoryJobQueue"]
