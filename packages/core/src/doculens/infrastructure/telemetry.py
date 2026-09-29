"""OpenTelemetry TracerProvider setup (SPECIFICATIONS.md §52, OQ-21).

Local development typically uses the console exporter; staging/production export OTLP/HTTP to the
ADOT collector or Lambda layer endpoint. Metrics remain EMF via structured logs
(:mod:`doculens.application.metrics`); this module configures **traces only**.

Callers must not put passwords, tokens, API keys, prompts, answers, or document text into spans.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

from doculens.infrastructure.config import OtelTracesExporter

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter

    from doculens.infrastructure.config import CoreSettings

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def configure_tracing(
    *,
    service_name: str,
    settings: CoreSettings,
    exporter: SpanExporter | None = None,
    service_version: str | None = None,
) -> TracerProvider | None:
    """Install the process-wide tracer provider when tracing is enabled.

    Idempotent for production: if an SDK ``TracerProvider`` is already installed (for example by
    the ADOT Lambda layer), this is a no-op unless a test ``exporter`` is passed, in which case a
    span processor is attached to the existing provider. The OpenTelemetry SDK does not allow
    replacing a provider once set.
    """
    global _provider  # noqa: PLW0603 - mirrors logging/metrics process singletons

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        _provider = existing
        if exporter is not None:
            existing.add_span_processor(SimpleSpanProcessor(exporter))
        return existing

    chosen = settings.otel_traces_exporter
    if exporter is None and chosen is OtelTracesExporter.NONE:
        return None

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name or service_name,
            "service.version": settings.otel_service_version or service_version or "0.0.0",
            "deployment.environment": settings.app_env.value,
        }
    )
    sampler = ParentBasedTraceIdRatio(settings.otel_traces_sampler_ratio)
    provider = TracerProvider(resource=resource, sampler=sampler)

    span_exporter = exporter if exporter is not None else _build_exporter(settings)
    if span_exporter is None:
        return None

    # Console is low-volume local debugging; OTLP and in-memory tests use batching / simple.
    if chosen is OtelTracesExporter.CONSOLE and exporter is None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        provider.add_span_processor(
            SimpleSpanProcessor(span_exporter)
            if exporter is not None
            else BatchSpanProcessor(span_exporter)
        )

    trace.set_tracer_provider(provider)
    _provider = provider
    logger.info(
        "tracing configured",
        extra={
            "operation": "telemetry.configure",
            "exporter": chosen.value if exporter is None else "custom",
            "service": settings.otel_service_name or service_name,
            "sample_ratio": settings.otel_traces_sampler_ratio,
        },
    )
    return provider


def shutdown_tracing() -> None:
    """Flush and shut down the provider installed by :func:`configure_tracing`."""
    global _provider  # noqa: PLW0603
    if _provider is None:
        return
    _provider.force_flush()
    _provider.shutdown()
    _provider = None


def _build_exporter(settings: CoreSettings) -> SpanExporter | None:
    if settings.otel_traces_exporter is OtelTracesExporter.NONE:
        return None
    if settings.otel_traces_exporter is OtelTracesExporter.CONSOLE:
        return ConsoleSpanExporter()
    # OTLP/HTTP — ADOT / collector locally or in AWS (OQ-21).
    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint:
        resolved = (
            endpoint
            if endpoint.rstrip("/").endswith("/v1/traces")
            else f"{endpoint.rstrip('/')}/v1/traces"
        )
        return OTLPSpanExporter(endpoint=resolved)
    return OTLPSpanExporter()


__all__ = ["configure_tracing", "shutdown_tracing"]
