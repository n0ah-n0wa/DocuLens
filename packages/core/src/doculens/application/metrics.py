"""Application metrics (SPECIFICATIONS.md §51, OQ-21).

Metrics are emitted as structured log lines shaped for CloudWatch Embedded Metric Format.
The logging pipeline adds the ``_aws`` envelope when ``metric=True`` is present. Locally the
same lines remain ordinary JSON with metric fields for debugging and tests.

Never put passwords, tokens, API keys, or document text into metric dimensions or properties.
Dimension keys that would explode cardinality (ids, paths, free-text errors) are dropped.
"""

import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("doculens.metrics")

NAMESPACE_API = "DocuLens/API"
NAMESPACE_DOCUMENTS = "DocuLens/Documents"
NAMESPACE_RAG = "DocuLens/RAG"
NAMESPACE_AI = "DocuLens/AI"

_UNIT_COUNT = "Count"
_UNIT_MILLISECONDS = "Milliseconds"

# Identifiers and free text must never become CloudWatch dimension values (§51 cost/cardinality).
_FORBIDDEN_DIMENSION_KEYS: frozenset[str] = frozenset(
    {
        "document_id",
        "user_id",
        "request_id",
        "job_id",
        "conversation_id",
        "path",
        "url",
        "query",
        "error",
        "message",
        "email",
        "filename",
        "diagnostics",
        "trace_id",
        "span_id",
        "traceparent",
    }
)
_MAX_DIMENSION_VALUE_LENGTH = 64


@dataclass(slots=True)
class MetricsConfig:
    service: str
    environment: str
    enabled: bool = True


_config = MetricsConfig(service="doculens", environment="local", enabled=True)


def configure_metrics(*, service: str, environment: str, enabled: bool = True) -> None:
    """Install the metric identity for this process (idempotent)."""
    global _config  # noqa: PLW0603 - process-wide singleton mirrors logging config
    _config = MetricsConfig(service=service, environment=environment, enabled=enabled)


def metrics_config() -> MetricsConfig:
    return _config


def emit_count(
    name: str,
    value: float = 1.0,
    *,
    namespace: str,
    dimensions: Mapping[str, str] | None = None,
) -> None:
    _emit(name, value, unit=_UNIT_COUNT, namespace=namespace, dimensions=dimensions)


def emit_latency_ms(
    name: str,
    value_ms: float,
    *,
    namespace: str,
    dimensions: Mapping[str, str] | None = None,
) -> None:
    _emit(name, value_ms, unit=_UNIT_MILLISECONDS, namespace=namespace, dimensions=dimensions)


@contextmanager
def measure_latency(
    name: str,
    *,
    namespace: str,
    dimensions: Mapping[str, str] | None = None,
) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        yield state
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        state["duration_ms"] = duration_ms
        emit_latency_ms(name, duration_ms, namespace=namespace, dimensions=dimensions)


def sanitize_dimensions(dimensions: Mapping[str, str] | None) -> dict[str, str]:
    """Drop high-cardinality keys and truncate values for safe EMF emission."""
    if not dimensions:
        return {}
    clean: dict[str, str] = {}
    for key, value in dimensions.items():
        if key.lower() in _FORBIDDEN_DIMENSION_KEYS:
            continue
        text = str(value)
        if len(text) > _MAX_DIMENSION_VALUE_LENGTH:
            text = text[:_MAX_DIMENSION_VALUE_LENGTH]
        clean[key] = text
    return clean


def build_emf_fields(
    name: str,
    value: float,
    *,
    unit: str,
    namespace: str,
    dimensions: Mapping[str, str],
) -> dict[str, Any]:
    """Pure helper used by the logging pipeline and unit tests."""
    dimension_keys = list(dimensions.keys())
    return {
        "metric": True,
        "metric_name": name,
        "metric_namespace": namespace,
        "metric_unit": unit,
        name: float(value),
        **dimensions,
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [dimension_keys],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
    }


def _emit(
    name: str,
    value: float,
    *,
    unit: str,
    namespace: str,
    dimensions: Mapping[str, str] | None,
) -> None:
    if not _config.enabled:
        return
    dim_map = {
        "service": _config.service,
        "environment": _config.environment,
        **sanitize_dimensions(dimensions),
    }
    fields = build_emf_fields(name, value, unit=unit, namespace=namespace, dimensions=dim_map)
    logger.info("metric", extra=fields)


__all__ = [
    "NAMESPACE_AI",
    "NAMESPACE_API",
    "NAMESPACE_DOCUMENTS",
    "NAMESPACE_RAG",
    "MetricsConfig",
    "build_emf_fields",
    "configure_metrics",
    "emit_count",
    "emit_latency_ms",
    "measure_latency",
    "metrics_config",
    "sanitize_dimensions",
]
