"""FastAPI application factory: the API's composition root.

``create_app`` loads and validates settings, configures logging, assembles the application
components, and wires middleware, error handlers and routers. It holds no module-level state, so
every call produces an independent application; uvicorn starts it with
``uvicorn doculens_api.main:create_app --factory``. Resources that need opening and closing
(connection pools, clients) are acquired in ``lifespan`` once adapters exist.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from doculens.application.health import HealthProbe, ReadinessService
from doculens.infrastructure.config import load_settings
from doculens.infrastructure.logging import configure_logging
from doculens_api import SERVICE_NAME, __version__
from doculens_api.dependencies import AppComponents
from doculens_api.errors import DEFAULT_ERROR_RESPONSES, register_error_handlers
from doculens_api.middleware.request_context import RequestContextMiddleware
from doculens_api.middleware.security_headers import SecurityHeadersMiddleware
from doculens_api.routers import health
from doculens_api.settings import ApiSettings

OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness and readiness probes for the platform."},
]

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def create_app(
    settings: ApiSettings | None = None, *, probes: Sequence[HealthProbe] = ()
) -> FastAPI:
    """Build a fully wired application instance.

    ``settings`` defaults to the validated environment configuration; ``probes`` are the dependency
    checks exposed by ``/health/ready``. Both are injectable so tests can run isolated instances.
    """
    resolved = settings if settings is not None else load_settings(ApiSettings)
    configure_logging(
        service=SERVICE_NAME,
        environment=resolved.app_env.value,
        level=resolved.log_level,
        log_format=resolved.log_format,
    )
    components = AppComponents(
        settings=resolved,
        readiness=ReadinessService(probes, timeout_seconds=resolved.health_probe_timeout_seconds),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application started",
            operation="app.start",
            version=__version__,
            environment=resolved.app_env.value,
            docs_enabled=resolved.docs_enabled,
        )
        try:
            yield
        finally:
            logger.info("application stopped", operation="app.stop")

    app = FastAPI(
        title="DocuLens API",
        version=__version__,
        description=(
            "Document intelligence and grounded question answering over PDF collections. "
            "All errors use the `ErrorResponse` envelope and every response carries the request "
            "correlation ID header."
        ),
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
        responses=DEFAULT_ERROR_RESPONSES,
        lifespan=lifespan,
    )
    app.state.components = components

    register_error_handlers(app, header_name=resolved.request_id_header)
    app.include_router(health.router)

    # Middleware added later wraps the earlier ones; the request-context middleware goes last so
    # that it is outermost and every response, including those from other middleware, is
    # correlated and access-logged.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, header_name=resolved.request_id_header)
    return app
