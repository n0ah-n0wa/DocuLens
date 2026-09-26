"""Job queue adapters and composition helpers (SPECIFICATIONS.md §48, ADR-006)."""

from typing import Protocol

from doculens.application.jobs import JobQueue
from doculens.infrastructure.config import CoreSettings, QueueBackend
from doculens.infrastructure.queue.memory import InMemoryJobQueue
from doculens.infrastructure.queue.redis import RedisJobQueue
from doculens.infrastructure.queue.sqs import SqsJobQueue

PROBE_NAME = "job-queue"


class CheckableJobQueue(JobQueue, Protocol):
    async def check(self) -> None: ...


def build_job_queue(settings: CoreSettings) -> JobQueue:
    if settings.queue_backend is QueueBackend.MEMORY:
        return InMemoryJobQueue()
    if settings.queue_backend is QueueBackend.REDIS:
        if settings.redis_url is None:
            message = "REDIS_URL is required when QUEUE_BACKEND=redis"
            raise ValueError(message)
        return RedisJobQueue.from_url(settings.redis_url, name=settings.queue_name)
    if settings.queue_sqs_url is None or settings.queue_sqs_region is None:
        message = "QUEUE_SQS_URL and QUEUE_SQS_REGION are required when QUEUE_BACKEND=sqs"
        raise ValueError(message)
    return SqsJobQueue.from_settings(
        queue_url=settings.queue_sqs_url,
        region=settings.queue_sqs_region,
        dead_letter_queue_url=settings.queue_sqs_dlq_url,
        endpoint_url=settings.queue_sqs_endpoint_url,
    )


class JobQueueProbe:
    """Readiness probe for ``/health/ready`` when the adapter can check itself."""

    name = PROBE_NAME

    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue

    async def check(self) -> None:
        checker = getattr(self._queue, "check", None)
        if checker is None:
            return
        await checker()


__all__ = [
    "PROBE_NAME",
    "CheckableJobQueue",
    "InMemoryJobQueue",
    "JobQueueProbe",
    "RedisJobQueue",
    "SqsJobQueue",
    "build_job_queue",
]
