"""AWS Lambda entrypoint for SQS-triggered document processing (provisional OQ-2).

The Terraform event-source mapping delivers messages; this handler processes each
record and reports partial batch failures so SQS redrive handles retries / DLQ.

Terminal processor outcomes (``failed``, ``skipped``) are acknowledged so validation
rejections do not burn receive counts into the DLQ. After SQS exhaustion the document
is marked ``FAILED`` so users are not left in a permanent mid-pipeline state.

Each invocation also runs a bounded ADR-011 reconciliation sweep for orphaned
``UPLOADED`` rows left behind by ``enqueue_quietly`` failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from doculens.application.jobs import DocumentJobDispatcher
from doculens.infrastructure.config import CoreSettings, load_settings
from doculens.infrastructure.logging import configure_logging
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.queue import build_job_queue
from doculens_worker import SERVICE_NAME, __version__
from doculens_worker.processing import build_processor, process_document

logger = logging.getLogger(__name__)

_SETTINGS: CoreSettings | None = None

# Outcomes that mean "work finished; do not retry this message".
_ACK_OUTCOMES = frozenset({"processed", "no_op", "concurrent", "failed", "skipped"})


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Process an SQS event batch; return ``batchItemFailures`` for retries."""
    del context
    settings = _settings()
    records = list(event.get("Records") or [])
    return asyncio.run(_handle_batch(settings, records))


async def _handle_batch(settings: CoreSettings, records: list[dict[str, Any]]) -> dict[str, Any]:
    await _reconcile_stragglers(settings)
    failures: list[dict[str, str]] = []
    for record in records:
        message_id = str(record.get("messageId", ""))
        try:
            await _process_record(settings, record)
        except Exception:
            logger.exception(
                "sqs record failed",
                extra={"operation": "worker.lambda", "message_id": message_id},
            )
            if message_id:
                failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def _settings() -> CoreSettings:
    global _SETTINGS  # noqa: PLW0603 - cold-start reuse
    if _SETTINGS is None:
        _SETTINGS = load_settings(CoreSettings, env_file=None)
        configure_logging(
            service=SERVICE_NAME,
            environment=_SETTINGS.app_env.value,
            level=_SETTINGS.log_level,
            log_format=_SETTINGS.log_format,
        )
        logger.info(
            "worker lambda configured",
            extra={"operation": "worker.lambda.start", "version": __version__},
        )
    return _SETTINGS


def _receive_count(record: dict[str, Any]) -> int:
    attrs = record.get("attributes") or {}
    raw = attrs.get("ApproximateReceiveCount", "1")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


async def _reconcile_stragglers(settings: CoreSettings) -> None:
    database = Database(settings)
    try:
        dispatcher = DocumentJobDispatcher(queue=build_job_queue(settings))
        await dispatcher.reconcile_stragglers(database.unit_of_work)
    except Exception:
        logger.exception(
            "straggler reconciliation failed",
            extra={"operation": "worker.lambda.reconcile"},
        )
    finally:
        await database.dispose()


async def _abandon_document(settings: CoreSettings, document_id: UUID, *, reason: str) -> None:
    database = Database(settings)
    try:
        processor = build_processor(settings, unit_of_work=database.unit_of_work)
        await processor.fail_permanently(document_id, reason=reason)
    except Exception:
        logger.exception(
            "failed to mark document FAILED after SQS exhaustion",
            extra={"operation": "worker.lambda.abandon", "document_id": str(document_id)},
        )
    finally:
        await database.dispose()


async def _process_record(settings: CoreSettings, record: dict[str, Any]) -> None:
    body_raw = record.get("body")
    if not isinstance(body_raw, str) or not body_raw:
        message = "SQS record missing body"
        raise ValueError(message)
    payload = json.loads(body_raw)
    if not isinstance(payload, dict):
        message = "SQS body must be a JSON object"
        raise TypeError(message)
    document_id = UUID(str(payload["document_id"]))
    receive_count = _receive_count(record)
    try:
        report = await process_document(settings, document_id)
    except Exception as exc:
        if receive_count >= settings.queue_max_attempts:
            await _abandon_document(
                settings,
                document_id,
                reason=f"sqs_exhausted:{type(exc).__name__}: {exc}",
            )
            return
        raise
    if report.outcome.value in _ACK_OUTCOMES:
        return
    message = f"processing outcome={report.outcome.value}"
    if receive_count >= settings.queue_max_attempts:
        await _abandon_document(settings, document_id, reason=message)
        return
    raise RuntimeError(message)
