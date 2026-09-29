import json
import logging
import time

import pytest

from doculens.application.metrics import (
    NAMESPACE_API,
    NAMESPACE_RAG,
    build_emf_fields,
    configure_metrics,
    emit_count,
    emit_latency_ms,
    measure_latency,
    metrics_config,
    sanitize_dimensions,
)
from doculens.infrastructure.config import LogFormat, LogLevel
from doculens.infrastructure.logging import configure_logging

pytestmark = pytest.mark.unit


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_build_emf_fields_include_cloudwatch_envelope() -> None:
    fields = build_emf_fields(
        "HttpRequests",
        1.0,
        unit="Count",
        namespace=NAMESPACE_API,
        dimensions={"service": "api", "environment": "test", "method": "GET"},
    )

    assert fields["metric"] is True
    assert fields["metric_name"] == "HttpRequests"
    assert fields["HttpRequests"] == 1.0
    assert fields["method"] == "GET"
    cloudwatch = fields["_aws"]["CloudWatchMetrics"][0]
    assert cloudwatch["Namespace"] == NAMESPACE_API
    assert cloudwatch["Metrics"] == [{"Name": "HttpRequests", "Unit": "Count"}]
    assert cloudwatch["Dimensions"] == [["service", "environment", "method"]]


def test_emit_count_writes_structured_metric_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )
    configure_metrics(service="doculens-test", environment="local")

    emit_count("RagAnswers", namespace=NAMESPACE_RAG, dimensions={"outcome": "answered"})

    entries = _json_lines(capsys.readouterr().out)
    metric = next(entry for entry in entries if entry.get("metric") is True)
    assert metric["message"] == "metric"
    assert metric["metric_name"] == "RagAnswers"
    assert metric["metric_namespace"] == NAMESPACE_RAG
    assert metric["outcome"] == "answered"
    assert metric["service"] == "doculens-test"
    assert metric["RagAnswers"] == 1.0


def test_emit_latency_and_measure_latency(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )
    configure_metrics(service="doculens-test", environment="local")

    emit_latency_ms(
        "HttpRequestDuration",
        12.5,
        namespace=NAMESPACE_API,
        dimensions={"method": "GET"},
    )
    with measure_latency("WorkDuration", namespace=NAMESPACE_API) as state:
        time.sleep(0.001)

    entries = _json_lines(capsys.readouterr().out)
    latencies = [entry for entry in entries if entry.get("metric") is True]
    assert latencies[0]["HttpRequestDuration"] == 12.5
    assert latencies[0]["metric_unit"] == "Milliseconds"
    assert isinstance(state["duration_ms"], float)
    assert state["duration_ms"] >= 0
    assert latencies[1]["WorkDuration"] == state["duration_ms"]


def test_metrics_can_be_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )
    configure_metrics(service="doculens-test", environment="local", enabled=False)
    assert metrics_config().enabled is False

    emit_count("HttpRequests", namespace=NAMESPACE_API)
    assert _json_lines(capsys.readouterr().out) == []

    configure_metrics(service="doculens-test", environment="local", enabled=True)
    # Keep the metrics logger level in sync with root after re-enable.
    logging.getLogger("doculens.metrics").setLevel(LogLevel.INFO.value)


def test_sanitize_dimensions_drops_high_cardinality_keys() -> None:
    cleaned = sanitize_dimensions(
        {
            "outcome": "answered",
            "document_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "user_id": "user-1",
            "request_id": "req-1",
            "path": "/api/v1/documents/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "error": "TypeError: secret details",
            "model": "text-embedding-3-small",
        }
    )
    assert cleaned == {"outcome": "answered", "model": "text-embedding-3-small"}
    assert "document_id" not in cleaned
    assert "error" not in cleaned


def test_sanitize_dimensions_truncates_long_values() -> None:
    cleaned = sanitize_dimensions({"model": "x" * 200})
    assert cleaned["model"] == "x" * 64
