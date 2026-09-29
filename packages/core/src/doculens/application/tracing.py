"""OpenTelemetry span helpers for application code (SPECIFICATIONS.md §52).

Uses only ``opentelemetry-api`` so spans are no-ops until the infrastructure layer installs a
``TracerProvider``. Span attributes are limited to safe identifiers and counts — never passwords,
tokens, API keys, prompts, answers, or document text (§68).
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

TRACER_NAME = "doculens"

# Keys that must never be set as span attributes even if a caller passes them.
_FORBIDDEN_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "authorization",
        "content",
        "body",
        "text",
        "prompt",
        "messages",
        "answer",
        "question",
        "query_text",
        "chunk_text",
        "page_text",
        "extracted_text",
        "evidence_text",
        "payload",
        "raw",
    }
)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def set_span_attributes(span: Span, attributes: Mapping[str, Any] | None) -> None:
    """Set only safe, non-null attributes on ``span``."""
    if not attributes:
        return
    for key, value in attributes.items():
        if value is None or key.lower() in _FORBIDDEN_ATTRIBUTE_KEYS:
            continue
        if isinstance(value, (bool, int, float, str)):
            span.set_attribute(key, value)
        else:
            span.set_attribute(key, str(value))


def record_span_error(span: Span, exc: BaseException) -> None:
    """Mark the span failed without recording exception messages (may contain private data)."""
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
    span.set_attribute("exception.type", type(exc).__name__)


@contextmanager
def start_span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[Span]:
    """Open a child span of the current context; record error type on failure."""
    with get_tracer().start_as_current_span(name, kind=kind) as span:
        set_span_attributes(span, attributes)
        try:
            yield span
        except Exception as exc:
            record_span_error(span, exc)
            raise


def inject_traceparent() -> str | None:
    """Serialize the current trace context for queue / async hand-off."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent")


@contextmanager
def attach_traceparent(traceparent: str | None) -> Iterator[None]:
    """Restore a parent context from a W3C ``traceparent`` (e.g. a job payload)."""
    if not traceparent:
        yield
        return
    ctx = extract({"traceparent": traceparent})
    token = otel_context.attach(ctx)
    try:
        yield
    finally:
        otel_context.detach(token)


__all__ = [
    "TRACER_NAME",
    "attach_traceparent",
    "get_tracer",
    "inject_traceparent",
    "record_span_error",
    "set_span_attributes",
    "start_span",
]
