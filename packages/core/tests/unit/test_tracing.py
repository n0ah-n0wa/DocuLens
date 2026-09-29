"""OpenTelemetry tracing helpers and configuration (SPECIFICATIONS.md §52)."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr, ValidationError

from doculens.application.tracing import (
    attach_traceparent,
    inject_traceparent,
    start_span,
)
from doculens.infrastructure.config import (
    CoreSettings,
    Environment,
    OtelTracesExporter,
)
from doculens.infrastructure.telemetry import configure_tracing

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://doculens:doculens@localhost:5432/doculens")

DEPLOYED_BASE = {
    "storage_backend": "s3",
    "storage_bucket": "doculens-prod-documents",
    "storage_region": "eu-central-1",
    "storage_encryption": "AES256",
    "embedding_provider": "openai",
    "llm_provider": "openai",
    "chroma_url": "https://chroma.internal:8000",
    "queue_backend": "sqs",
    "queue_sqs_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens",
    "queue_sqs_dlq_url": "https://sqs.eu-central-1.amazonaws.com/123456789012/doculens-dlq",
    "queue_sqs_region": "eu-central-1",
    "rate_limit_backend": "redis",
    "redis_url": "rediss://redis.internal:6379/0",
    "storage_expected_bucket_owner": "123456789012",
    "chroma_api_token": SecretStr("chroma-deployed-token"),
}


@pytest.fixture
def memory_tracer() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        otel_traces_exporter=OtelTracesExporter.OTLP,
        otel_exporter_otlp_endpoint="http://127.0.0.1:4318",
    )
    configure_tracing(
        service_name="doculens-test",
        settings=settings,
        exporter=exporter,
        service_version="test",
    )
    exporter.clear()
    yield exporter
    exporter.clear()


def test_start_span_records_safe_attributes_only(memory_tracer: InMemorySpanExporter) -> None:
    with start_span(
        "answering.answer",
        attributes={
            "user_id": str(uuid4()),
            "outcome": "answered",
            "password": "hunter2",
            "prompt": "secret prompt text",
            "question": "what is in the contract?",
        },
    ):
        pass

    spans = memory_tracer.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["outcome"] == "answered"
    assert "user_id" in attrs
    assert "password" not in attrs
    assert "prompt" not in attrs
    assert "question" not in attrs


def test_traceparent_round_trips_across_attach(memory_tracer: InMemorySpanExporter) -> None:
    with start_span("jobs.enqueue", attributes={"job_id": "j1"}):
        carrier = inject_traceparent()
    assert carrier is not None
    assert carrier.startswith("00-")

    with attach_traceparent(carrier), start_span("jobs.process"):
        pass

    spans = memory_tracer.get_finished_spans()
    by_name = {span.name: span for span in spans}
    parent = by_name["jobs.enqueue"]
    child = by_name["jobs.process"]
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id
    assert child.context.trace_id == parent.context.trace_id


def test_configure_tracing_none_is_a_noop() -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        otel_traces_exporter=OtelTracesExporter.NONE,
    )
    # When a provider is already installed (common in the test process), NONE does not attach
    # exporters; when none is installed, configure returns None.
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        assert configure_tracing(service_name="doculens-test", settings=settings) is existing
    else:
        assert configure_tracing(service_name="doculens-test", settings=settings) is None


def test_deployed_rejects_console_exporter() -> None:
    with pytest.raises(ValidationError, match="OTEL_TRACES_EXPORTER"):
        CoreSettings(
            _env_file=None,
            database_url=DB_URL,
            app_env=Environment.PRODUCTION,
            otel_traces_exporter=OtelTracesExporter.CONSOLE,
            **DEPLOYED_BASE,  # type: ignore[arg-type]
        )


def test_deployed_otlp_requires_endpoint() -> None:
    with pytest.raises(ValidationError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        CoreSettings(
            _env_file=None,
            database_url=DB_URL,
            app_env=Environment.PRODUCTION,
            otel_traces_exporter=OtelTracesExporter.OTLP,
            otel_exporter_otlp_endpoint=None,
            **DEPLOYED_BASE,  # type: ignore[arg-type]
        )
