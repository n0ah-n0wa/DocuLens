"""Process entrypoint for the document-processing worker: the worker's composition root.

With no arguments the process consumes the configured job queue (ADR-006). ``process
<document-id>`` still runs the pipeline once for local debugging and operators.
"""

import asyncio
import sys
from collections.abc import Sequence
from typing import Final
from uuid import UUID

import structlog

from doculens.infrastructure.config import ConfigurationError, CoreSettings, load_settings
from doculens.infrastructure.logging import configure_logging
from doculens_worker import SERVICE_NAME, __version__
from doculens_worker.processing import process_document, run_worker

EXIT_OK: Final = 0
EXIT_PROCESSING_FAILED: Final = 1
EXIT_CONFIGURATION_ERROR: Final = 2
EXIT_USAGE_ERROR: Final = 3
USAGE: Final = "usage: doculens-worker [process <document-id>]"


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0911 - exit codes map 1:1 to outcomes
    """Start the worker process and return its exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = _parse(arguments)
    if command is None:
        sys.stderr.write(USAGE + "\n")
        return EXIT_USAGE_ERROR
    try:
        settings = load_settings(CoreSettings)
    except ConfigurationError as exc:
        # Logging is not configured yet, so the message goes to stderr in plain text.
        sys.stderr.write(f"{SERVICE_NAME}: {exc}\n")
        return EXIT_CONFIGURATION_ERROR

    configure_logging(
        service=SERVICE_NAME,
        environment=settings.app_env.value,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)
    if command == "process":
        document_id = UUID(arguments[1])
        try:
            report = asyncio.run(process_document(settings, document_id))
        except Exception as exc:
            logger.exception(
                "document processing aborted",
                operation="worker.process",
                document_id=str(document_id),
                error_type=type(exc).__name__,
            )
            return EXIT_PROCESSING_FAILED
        logger.info(
            "document processed",
            operation="worker.process",
            document_id=str(document_id),
            outcome=report.outcome.value,
            status=report.status.value if report.status is not None else None,
            stages=[stage.value for stage in report.stages],
        )
        if report.outcome.value in {"processed", "no_op", "concurrent"}:
            return EXIT_OK
        return EXIT_PROCESSING_FAILED
    logger.info(
        "worker started",
        operation="worker.start",
        version=__version__,
        queue_backend=settings.queue_backend.value,
        handlers_registered=1,
    )
    idle_polls = _max_idle_polls()
    try:
        asyncio.run(run_worker(settings, max_idle_polls=idle_polls))
    except KeyboardInterrupt:
        logger.info("worker stopped", operation="worker.stop")
        return EXIT_OK
    except Exception:
        logger.exception("worker aborted", operation="worker.abort")
        return EXIT_PROCESSING_FAILED
    return EXIT_OK


def _max_idle_polls() -> int | None:
    """Optional smoke-test bound: exit after N empty polls (unset means run forever)."""
    raw = __import__("os").environ.get("DOCULENS_WORKER_MAX_IDLE_POLLS")
    if raw is None or raw.strip() == "":
        return None
    return max(1, int(raw))


def _parse(arguments: list[str]) -> str | None:
    """``[]`` starts the worker; ``["process", "<uuid>"]`` runs one document; else usage."""
    if not arguments:
        return "start"
    if len(arguments) == 2 and arguments[0] == "process":  # noqa: PLR2004 - verb plus argument
        try:
            UUID(arguments[1])
        except ValueError:
            return None
        return "process"
    return None
