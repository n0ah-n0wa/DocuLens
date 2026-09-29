"""CloudWatch EMF helpers re-exported for adapters and composition roots.

Prefer :mod:`doculens.application.metrics` from application code. This module exists so
infrastructure adapters can import metrics next to logging without reaching across packages
awkwardly.
"""

from doculens.application.metrics import (
    NAMESPACE_AI,
    NAMESPACE_API,
    NAMESPACE_DOCUMENTS,
    NAMESPACE_RAG,
    MetricsConfig,
    build_emf_fields,
    configure_metrics,
    emit_count,
    emit_latency_ms,
    measure_latency,
    metrics_config,
    sanitize_dimensions,
)

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
