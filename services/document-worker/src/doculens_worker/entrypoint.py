"""Process entrypoint for the document-processing worker.

No job handlers exist yet. The entrypoint establishes the process contract (logging setup, exit
code) used by the container image and by tests; the queue consumer is registered in a later phase.
"""

import logging

from doculens_worker import __version__

logger = logging.getLogger(__name__)


def main() -> int:
    """Start the worker process and return its exit code."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("doculens-worker %s started; no job handlers are registered", __version__)
    return 0
