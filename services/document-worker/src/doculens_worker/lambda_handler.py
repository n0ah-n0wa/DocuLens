"""AWS Lambda entrypoint for SQS-triggered document processing (provisional OQ-2).

The Terraform event-source mapping delivers messages; this handler processes each
record and reports partial batch failures so SQS redrive handles retries / DLQ.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from doculens.infrastructure.config import CoreSettings, load_settings
from doculens.infrastructure.logging import configure_logging
from doculens_worker import SERVICE_NAME, __version__
from doculens_worker.processing import process_document

logger = logging.getLogger(__name__)

_SETTINGS: CoreSettings | None = None


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Process an SQS event batch; return ``batchItemFailures`` for retries."""
    del context
    settings = _settings()
    records = list(event.get("Records") or [])
    return asyncio.run(_handle_batch(settings, records))


async def _handle_batch(settings: CoreSettings, records: list[dict[str, Any]]) -> dict[str, Any]:
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
    report = await process_document(settings, document_id)
    if report.outcome.value not in {"processed", "no_op", "concurrent"}:
        message = f"processing outcome={report.outcome.value}"
        raise RuntimeError(message)
