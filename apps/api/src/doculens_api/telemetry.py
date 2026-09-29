"""FastAPI OpenTelemetry instrumentation (SPECIFICATIONS.md §52).

Does not capture request/response bodies or Authorization headers — only safe correlation headers
and route metadata.
"""

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor


def instrument_fastapi(app: FastAPI) -> None:
    """Attach ASGI middleware that creates a SERVER span per HTTP request."""
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health/live,health/ready",
        http_capture_headers_server_request=["x-request-id"],
        http_capture_headers_server_response=["x-request-id"],
    )


__all__ = ["instrument_fastapi"]
