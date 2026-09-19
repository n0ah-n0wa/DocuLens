"""Process entrypoint for the document-processing worker: the worker's composition root.

No job handlers exist yet. The entrypoint establishes the process contract (validated settings,
structured logging, exit codes) used by the container image and by tests; the queue consumer is
registered in a later phase.
"""

import sys
from typing import Final

import structlog

from doculens.infrastructure.config import ConfigurationError, CoreSettings, load_settings
from doculens.infrastructure.logging import configure_logging
from doculens_worker import SERVICE_NAME, __version__

EXIT_OK: Final = 0
EXIT_CONFIGURATION_ERROR: Final = 2


def main() -> int:
    """Start the worker process and return its exit code."""
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
    logger.info(
        "worker started", operation="worker.start", version=__version__, handlers_registered=0
    )
    return EXIT_OK
