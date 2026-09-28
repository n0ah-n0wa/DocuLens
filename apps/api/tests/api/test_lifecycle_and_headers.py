"""Start-up/shutdown lifecycle, security headers and deployed-environment defaults."""

import json
from http import HTTPStatus

import pytest
from fastapi import APIRouter, FastAPI, Response
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient as StarletteClient

from doculens.infrastructure.config import (
    EmbeddingProviderKind,
    Environment,
    LLMProviderKind,
    LogLevel,
    QueueBackend,
    RateLimitBackend,
    StorageBackend,
    StorageEncryption,
)
from doculens_api.main import create_app
from doculens_api.middleware.security_headers import SecurityHeadersMiddleware
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api

DB_URL = SecretStr("postgresql+asyncpg://doculens:not-a-secret@127.0.0.1:1/doculens")
JWT = SecretStr("lifecycle-test-secret-that-is-at-least-32-bytes")

_DEPLOYED_PLATFORM = {
    "storage_backend": StorageBackend.S3,
    "storage_bucket": "doculens-test-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": StorageEncryption.AES256,
    "embedding_provider": EmbeddingProviderKind.OPENAI,
    "llm_provider": LLMProviderKind.OPENAI,
    "chroma_url": "https://chroma.internal:8000",
    "queue_backend": QueueBackend.SQS,
    "queue_sqs_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens",
    "queue_sqs_dlq_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens-dlq",
    "queue_sqs_region": "eu-central-1",
    "rate_limit_backend": RateLimitBackend.REDIS,
    "redis_url": "rediss://redis.internal:6379/0",
    "storage_expected_bucket_owner": "123456789012",
    "chroma_api_token": SecretStr("chroma-deployed-token"),
}


def _entries(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_lifespan_logs_start_and_stop_events(
    capsys: pytest.CaptureFixture[str], settings: ApiSettings
) -> None:
    app = create_app(settings.model_copy(update={"log_level": LogLevel.INFO}))

    with TestClient(app):
        pass

    operations = [entry["operation"] for entry in _entries(capsys.readouterr().out)]
    assert operations == ["app.start", "app.stop"]


def test_every_response_carries_baseline_security_headers(client: TestClient) -> None:
    for path in ("/health/live", "/does-not-exist"):
        response = client.get(path)

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Permissions-Policy"] == (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        assert (
            response.headers["Content-Security-Policy"]
            == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"
        assert "Strict-Transport-Security" not in response.headers


def test_hsts_is_emitted_when_enabled() -> None:
    async def ok(_: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/ping", ok)])
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=True)
    with StarletteClient(app) as client:
        response = client.get("/ping")

    assert response.headers["Strict-Transport-Security"] == ("max-age=31536000; includeSubDomains")


def test_docs_default_to_enabled_locally_and_disabled_when_deployed() -> None:
    local = ApiSettings(
        _env_file=None, app_env=Environment.LOCAL, database_url=DB_URL, jwt_secret=JWT
    )
    staging = ApiSettings(
        _env_file=None,
        app_env=Environment.STAGING,
        database_url=DB_URL,
        jwt_secret=JWT,
        **_DEPLOYED_PLATFORM,  # type: ignore[arg-type]
    )
    opted_in = ApiSettings(
        _env_file=None,
        app_env=Environment.PRODUCTION,
        api_docs_enabled=True,
        database_url=DB_URL,
        jwt_secret=JWT,
        **_DEPLOYED_PLATFORM,  # type: ignore[arg-type]
    )

    assert local.docs_enabled is True
    assert staging.docs_enabled is False
    assert opted_in.docs_enabled is True


def test_deployed_application_does_not_serve_docs_unless_opted_in(settings: ApiSettings) -> None:
    deployed = settings.model_copy(update={"app_env": Environment.STAGING})

    with TestClient(create_app(deployed)) as client:
        assert client.get("/openapi.json").status_code == HTTPStatus.NOT_FOUND
        assert client.get("/health/live").status_code == HTTPStatus.OK


def test_a_handler_may_override_a_default_security_header(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/__probe/cacheable")
    async def cacheable() -> Response:
        return Response(content="ok", headers={"Cache-Control": "max-age=60"})

    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/__probe/cacheable")

    assert response.headers["Cache-Control"] == "max-age=60"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_probe_timeout_is_configurable_and_bounded() -> None:
    configured = ApiSettings(
        _env_file=None, health_probe_timeout_seconds=0.5, database_url=DB_URL, jwt_secret=JWT
    )

    assert configured.health_probe_timeout_seconds == 0.5

    with pytest.raises(ValueError, match="health_probe_timeout_seconds"):
        ApiSettings(
            _env_file=None, health_probe_timeout_seconds=0, database_url=DB_URL, jwt_secret=JWT
        )
