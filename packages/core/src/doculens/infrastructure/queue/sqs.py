"""AWS SQS job queue adapter (SPECIFICATIONS.md §48).

Application retries hide the current delivery with ``ChangeMessageVisibility`` (no delete+resend),
so a crash mid-retry cannot lose the only copy of the message. ``ApproximateReceiveCount`` is the
attempt counter on the next claim. Explicit ``dead_letter`` copies to the configured DLQ then
deletes; poison payloads are dead-lettered the same way.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from doculens.domain.jobs import (
    ClaimedJob,
    DeadLetterRecord,
    ProcessingJob,
    QueueUnavailableError,
)
from doculens.domain.time import utc_now


def _job_payload(job: ProcessingJob) -> dict[str, Any]:
    return {
        "document_id": str(job.document_id),
        "job_id": job.job_id,
        "attempt": job.attempt,
        "enqueued_at": job.enqueued_at.astimezone(UTC).isoformat(),
        "request_id": job.request_id,
    }


def _job_from_payload(payload: dict[str, Any]) -> ProcessingJob:
    return ProcessingJob(
        document_id=UUID(str(payload["document_id"])),
        job_id=str(payload["job_id"]),
        attempt=int(payload["attempt"]),
        enqueued_at=datetime.fromisoformat(str(payload["enqueued_at"])),
        request_id=str(payload["request_id"]) if payload.get("request_id") else None,
    )


class SqsJobQueue:
    def __init__(
        self,
        client: Any,  # noqa: ANN401 - boto3 SQS client is stubbed only with extras
        *,
        queue_url: str,
        dead_letter_queue_url: str | None = None,
    ) -> None:
        self._client = client
        self._queue_url = queue_url
        self._dlq_url = dead_letter_queue_url

    @classmethod
    def from_settings(
        cls,
        *,
        queue_url: str,
        region: str,
        dead_letter_queue_url: str | None = None,
        endpoint_url: str | None = None,
    ) -> SqsJobQueue:
        client = boto3.client(
            "sqs",
            region_name=region,
            endpoint_url=endpoint_url,
            config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
        )
        return cls(client, queue_url=queue_url, dead_letter_queue_url=dead_letter_queue_url)

    async def enqueue(self, job: ProcessingJob) -> None:
        try:
            await _run(
                self._client.send_message,
                QueueUrl=self._queue_url,
                MessageBody=json.dumps(_job_payload(job)),
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError from exc

    async def claim(self, *, visibility_timeout_seconds: float) -> ClaimedJob | None:
        try:
            response = await _run(
                self._client.receive_message,
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=0,
                VisibilityTimeout=int(max(1, visibility_timeout_seconds)),
                AttributeNames=["ApproximateReceiveCount"],
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError from exc
        messages = response.get("Messages") or []
        if not messages:
            return None
        message = messages[0]
        receipt = str(message["ReceiptHandle"])
        body_text = str(message["Body"])
        try:
            body = json.loads(body_text)
            job = _job_from_payload(body)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            await self._dead_letter_raw(receipt, body_text, error=f"POISON_JOB: {exc}")
            return await self.claim(visibility_timeout_seconds=visibility_timeout_seconds)
        receive_count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        if receive_count > job.attempt:
            job = ProcessingJob(
                document_id=job.document_id,
                job_id=job.job_id,
                attempt=receive_count,
                enqueued_at=job.enqueued_at,
                request_id=job.request_id,
            )
        return ClaimedJob(receipt=receipt, job=job, available_at=utc_now())

    async def acknowledge(self, claim: ClaimedJob) -> None:
        try:
            await _run(
                self._client.delete_message,
                QueueUrl=self._queue_url,
                ReceiptHandle=claim.receipt,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError from exc

    async def retry(
        self, claim: ClaimedJob, *, delay_seconds: float, error: str
    ) -> ClaimedJob | None:
        """Hide the message until ``delay_seconds``; do not delete+resend (avoids loss on crash)."""
        del error
        delay = min(43_200, max(0, int(delay_seconds)))
        try:
            await _run(
                self._client.change_message_visibility,
                QueueUrl=self._queue_url,
                ReceiptHandle=claim.receipt,
                VisibilityTimeout=delay,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError from exc
        return None

    async def dead_letter(self, claim: ClaimedJob, *, error: str) -> None:
        if self._dlq_url is None:
            message = "QUEUE_SQS_DLQ_URL is required to dead-letter SQS messages"
            raise QueueUnavailableError(message)
        try:
            await _run(
                self._client.send_message,
                QueueUrl=self._dlq_url,
                MessageBody=json.dumps(
                    {
                        "job": _job_payload(claim.job),
                        "error": error,
                        "dead_lettered_at": utc_now().astimezone(UTC).isoformat(),
                    }
                ),
            )
            await _run(
                self._client.delete_message,
                QueueUrl=self._queue_url,
                ReceiptHandle=claim.receipt,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError from exc

    async def list_dead_letters(self, *, limit: int = 100) -> list[DeadLetterRecord]:
        if self._dlq_url is None:
            return []
        try:
            response = await _run(
                self._client.receive_message,
                QueueUrl=self._dlq_url,
                MaxNumberOfMessages=min(10, limit),
                WaitTimeSeconds=0,
                VisibilityTimeout=0,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError from exc
        records: list[DeadLetterRecord] = []
        for message in response.get("Messages") or []:
            payload = json.loads(message["Body"])
            records.append(
                DeadLetterRecord(
                    job=_job_from_payload(payload["job"]),
                    error=str(payload["error"]),
                    dead_lettered_at=datetime.fromisoformat(str(payload["dead_lettered_at"])),
                )
            )
        return records

    async def check(self) -> None:
        await _run(
            self._client.get_queue_attributes,
            QueueUrl=self._queue_url,
            AttributeNames=["QueueArn"],
        )

    async def _dead_letter_raw(self, receipt: str, body: str, *, error: str) -> None:
        try:
            if self._dlq_url is not None:
                await _run(
                    self._client.send_message,
                    QueueUrl=self._dlq_url,
                    MessageBody=json.dumps(
                        {
                            "job": {"raw": body},
                            "error": error,
                            "dead_lettered_at": utc_now().astimezone(UTC).isoformat(),
                        }
                    ),
                )
            await _run(
                self._client.delete_message,
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError from exc


async def _run(func: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - boto3 returns untyped dicts
    """Run a blocking boto3 call off the event loop."""
    return await asyncio.to_thread(func, **kwargs)


__all__ = ["SqsJobQueue"]
