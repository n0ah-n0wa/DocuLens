"""Structured logging (SPECIFICATIONS.md §50, §68).

Every process emits one JSON object per line (console rendering is for local development only)
carrying ``timestamp``, ``level``, ``logger``, ``service``, ``environment``, ``message`` and
whatever the current request or job has bound through structlog context variables (``request_id``,
``user_id``, ``document_id``, ...). Standard-library loggers, including uvicorn's, are routed
through the same pipeline so that every line has one shape. Callers are responsible for never
binding secrets or document contents.
"""

import logging
import sys

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from doculens.infrastructure.config import LogFormat, LogLevel

_OWNED_HANDLER_MARK = "_doculens_handler"


def configure_logging(
    *, service: str, environment: str, level: LogLevel, log_format: LogFormat
) -> None:
    """Configure structlog and the standard library root logger for this process.

    Calling it again replaces the handler it installed earlier and leaves handlers installed by
    others (test frameworks, host runtimes) untouched.
    """

    def add_service_context(_logger: WrappedLogger, _method: str, event: EventDict) -> EventDict:
        event.setdefault("service", service)
        event.setdefault("environment", environment)
        return event

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_service_context,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.EventRenamer("message"),
    ]

    if log_format is LogFormat.JSON:
        rendering: list[Processor] = [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        rendering = [structlog.dev.ConsoleRenderer(event_key="message")]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *rendering],
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _OWNED_HANDLER_MARK, True)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _OWNED_HANDLER_MARK, False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.value)

    # uvicorn installs its own handlers; route its records through the shared pipeline instead.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # The API middleware writes the single structured access-log entry per request; uvicorn's own
    # access lines would duplicate it without the request context.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
