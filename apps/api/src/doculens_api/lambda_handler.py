"""AWS Lambda entrypoint for the DocuLens HTTP API (provisional OQ-2: Mangum + RIC).

Local/container CMD remains uvicorn. Lambda overrides ``image_config`` to run
``python -m awslambdaric`` with this module's ``handler``.
"""

from __future__ import annotations

from typing import Any

from mangum import Mangum

from doculens_api.main import create_app

_handler: Mangum | None = None


def handler(event: dict[str, Any], context: Any) -> Any:  # noqa: ANN401 - AWS Lambda context type
    """API Gateway HTTP API → FastAPI via Mangum."""
    global _handler  # noqa: PLW0603 - cold-start reuse
    if _handler is None:
        _handler = Mangum(create_app(), lifespan="on")
    return _handler(event, context)
