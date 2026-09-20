"""FastAPI application factory: the API's composition root.

``create_app`` loads and validates settings, configures logging, assembles the application
components, and wires middleware, error handlers and routers. It holds no module-level state, so
every call produces an independent application; uvicorn starts it with
``uvicorn doculens_api.main:create_app --factory``. The database engine is created here (no
connection is opened until first use) and disposed of when the application shuts down.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta

import structlog
from fastapi import FastAPI

from doculens.application.auth import AuthConfig, AuthService
from doculens.application.collections import CollectionService
from doculens.application.conversations import ConversationService
from doculens.application.documents import DocumentService
from doculens.application.health import HealthProbe, ReadinessService
from doculens.application.ratelimit import RateLimiter
from doculens.application.unit_of_work import UnitOfWorkFactory
from doculens.domain.auth import PasswordPolicy
from doculens.domain.time import utc_now
from doculens.infrastructure.config import load_settings
from doculens.infrastructure.logging import configure_logging
from doculens.infrastructure.persistence.database import Database, DatabaseProbe
from doculens.infrastructure.ratelimit import InMemoryRateLimiter
from doculens.infrastructure.security.passwords import Argon2PasswordHasher
from doculens.infrastructure.security.tokens import JwtTokenCodec
from doculens.infrastructure.storage import ObjectStorageProbe, build_object_storage
from doculens.infrastructure.vectors import ChromaVectorStore, VectorStoreProbe, build_vector_store
from doculens_api import SERVICE_NAME, __version__
from doculens_api.dependencies import AppComponents
from doculens_api.errors import DEFAULT_ERROR_RESPONSES, register_error_handlers
from doculens_api.middleware.request_context import RequestContextMiddleware
from doculens_api.middleware.security_headers import SecurityHeadersMiddleware
from doculens_api.routers import auth, collections, conversations, documents, health, users
from doculens_api.settings import ApiSettings

OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness and readiness probes for the platform."},
    {"name": "auth", "description": "Registration, login, token refresh and logout."},
    {"name": "users", "description": "The authenticated user's account."},
    {"name": "collections", "description": "Groupings of the user's documents."},
    {"name": "documents", "description": "Document metadata and processing status."},
    {"name": "conversations", "description": "Conversations and their cited messages."},
]

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def create_app(
    settings: ApiSettings | None = None,
    *,
    probes: Sequence[HealthProbe] | None = None,
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Build a fully wired application instance.

    ``settings`` defaults to the validated environment configuration. ``probes`` are the dependency
    checks exposed by ``/health/ready`` (``None`` means the real database). ``unit_of_work_factory``
    defaults to the PostgreSQL unit of work; tests may pass an in-memory one. ``rate_limiter``
    defaults to the in-process limiter (§37) until the Redis adapter exists.
    """
    resolved = settings if settings is not None else load_settings(ApiSettings)
    configure_logging(
        service=SERVICE_NAME,
        environment=resolved.app_env.value,
        level=resolved.log_level,
        log_format=resolved.log_format,
    )
    database = Database(resolved)
    object_storage = build_object_storage(resolved)
    vector_store = build_vector_store(resolved)
    default_probes: list[HealthProbe] = [
        DatabaseProbe(database),
        ObjectStorageProbe(object_storage),
        *([VectorStoreProbe(vector_store)] if isinstance(vector_store, ChromaVectorStore) else []),
    ]
    readiness_probes = probes if probes is not None else default_probes
    unit_of_work = (
        unit_of_work_factory if unit_of_work_factory is not None else database.unit_of_work
    )
    auth_service = AuthService(
        unit_of_work=unit_of_work,
        hasher=Argon2PasswordHasher(),
        codec=JwtTokenCodec(
            secret=resolved.jwt_secret.get_secret_value(),
            issuer=resolved.jwt_issuer,
            audience=resolved.jwt_audience,
            clock=utc_now,
        ),
        clock=utc_now,
        config=AuthConfig(
            access_token_ttl=timedelta(seconds=resolved.access_token_ttl_seconds),
            refresh_token_ttl=timedelta(seconds=resolved.refresh_token_ttl_seconds),
            password_policy=PasswordPolicy(min_length=resolved.password_min_length),
        ),
    )
    components = AppComponents(
        settings=resolved,
        database=database,
        readiness=ReadinessService(
            readiness_probes, timeout_seconds=resolved.health_probe_timeout_seconds
        ),
        auth=auth_service,
        collections=CollectionService(unit_of_work=unit_of_work),
        documents=DocumentService(unit_of_work=unit_of_work, vectors=vector_store),
        conversations=ConversationService(unit_of_work=unit_of_work),
        rate_limiter=rate_limiter if rate_limiter is not None else InMemoryRateLimiter(),
        object_storage=object_storage,
        vector_store=vector_store,
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
            await database.dispose()
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
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(collections.router)
    app.include_router(documents.router)
    app.include_router(conversations.router)

    # Middleware added later wraps the earlier ones; the request-context middleware goes last so
    # that it is outermost and every response, including those from other middleware, is
    # correlated and access-logged.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, header_name=resolved.request_id_header)
    return app
