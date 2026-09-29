import json
import logging

import pytest
import structlog
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from doculens.application.tracing import start_span
from doculens.infrastructure.config import CoreSettings, LogFormat, LogLevel, OtelTracesExporter
from doculens.infrastructure.logging import configure_logging, redact_sensitive_event
from doculens.infrastructure.telemetry import configure_tracing

pytestmark = pytest.mark.unit


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_json_lines_carry_the_shared_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-1")

    structlog.get_logger("doculens.test").info("something happened", operation="unit.test")
    structlog.contextvars.clear_contextvars()

    (entry,) = _json_lines(capsys.readouterr().out)
    assert entry["message"] == "something happened"
    assert entry["level"] == "info"
    assert entry["logger"] == "doculens.test"
    assert entry["service"] == "doculens-test"
    assert entry["environment"] == "local"
    assert entry["request_id"] == "req-1"
    assert entry["operation"] == "unit.test"
    assert str(entry["timestamp"]).endswith("Z")


def test_standard_library_records_use_the_same_pipeline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )

    logging.getLogger("uvicorn.error").warning("stdlib %s", "message")

    (entry,) = _json_lines(capsys.readouterr().out)
    assert entry["message"] == "stdlib message"
    assert entry["logger"] == "uvicorn.error"
    assert entry["level"] == "warning"
    assert entry["service"] == "doculens-test"


def test_standard_library_extra_fields_become_structured_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Application-layer security events log through stdlib ``extra``; the fields must land in
    the JSON entry rather than being dropped."""
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )

    logging.getLogger("doculens.application.auth").info(
        "login failed", extra={"operation": "auth.login_failed", "reason": "invalid_credentials"}
    )

    (entry,) = _json_lines(capsys.readouterr().out)
    assert entry["message"] == "login failed"
    assert entry["operation"] == "auth.login_failed"
    assert entry["reason"] == "invalid_credentials"


def test_uvicorn_access_lines_are_suppressed_in_favour_of_the_middleware_entry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )

    logging.getLogger("uvicorn.access").info('127.0.0.1 - "GET /health/live HTTP/1.1" 200')
    logging.getLogger("uvicorn.error").info("Application startup complete.")

    entries = _json_lines(capsys.readouterr().out)
    assert [entry["logger"] for entry in entries] == ["uvicorn.error"]


def test_records_below_the_configured_level_are_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        service="doculens-test",
        environment="local",
        level=LogLevel.WARNING,
        log_format=LogFormat.JSON,
    )

    structlog.get_logger("doculens.test").info("hidden")
    structlog.get_logger("doculens.test").error("shown")

    entries = _json_lines(capsys.readouterr().out)
    assert [entry["message"] for entry in entries] == ["shown"]


def _fail_with_a_secret_in_scope() -> None:
    secret_local = "hunter2-must-never-be-logged"  # noqa: S105 gitleaks:allow (test sentinel)
    message = f"boom ({len(secret_local)} chars in scope)"
    raise RuntimeError(message)


def test_exceptions_are_rendered_as_structured_data_without_locals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )

    try:
        _fail_with_a_secret_in_scope()
    except RuntimeError:
        structlog.get_logger("doculens.test").exception("failed")

    (entry,) = _json_lines(capsys.readouterr().out)
    rendered = json.dumps(entry["exception"])
    assert entry["level"] == "error"
    assert isinstance(entry["exception"], list)
    assert "RuntimeError" in rendered
    assert "_fail_with_a_secret_in_scope" in rendered
    assert "hunter2" not in rendered
    frames = [frame for exception in entry["exception"] for frame in exception["frames"]]
    assert frames
    assert all("locals" not in frame for frame in frames)


def test_console_exceptions_do_not_print_locals(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        service="doculens-test",
        environment="local",
        level=LogLevel.INFO,
        log_format=LogFormat.CONSOLE,
    )

    try:
        _fail_with_a_secret_in_scope()
    except RuntimeError:
        structlog.get_logger("doculens.test").exception("failed")

    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "hunter2" not in output


def test_console_format_renders_without_error(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        service="doculens-test",
        environment="local",
        level=LogLevel.INFO,
        log_format=LogFormat.CONSOLE,
    )

    structlog.get_logger("doculens.test").info("readable")

    output = capsys.readouterr().out
    assert "readable" in output
    assert "doculens-test" in output


def test_sensitive_keys_are_redacted_from_structured_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )

    # Sentinel values prove redaction; never appear in the rendered log line.
    fields = {
        "password": "hunter2-must-never-be-logged",
        "api_key": "sk-secret-must-never-be-logged",
        "access_token": "tok-must-never-be-logged",
        "authorization": "Bearer tok-must-never-be-logged",
        "prompt": "ignore previous instructions",
        "question": "what is in the contract?",
        "content": "full private document body",
        "input_tokens": 12,
        "output_tokens": 4,
        "user_id": "user-1",
        "request_id": "req-9",
    }
    structlog.get_logger("doculens.test").info("auth attempt", **fields)

    (entry,) = _json_lines(capsys.readouterr().out)
    redacted = "[redacted]"
    assert entry["password"] == redacted
    assert entry["api_key"] == redacted
    assert entry["access_token"] == redacted
    assert entry["authorization"] == redacted
    assert entry["prompt"] == redacted
    assert entry["question"] == redacted
    assert entry["content"] == redacted
    assert entry["input_tokens"] == 12
    assert entry["output_tokens"] == 4
    assert entry["user_id"] == "user-1"
    assert entry["request_id"] == "req-9"
    rendered = json.dumps(entry)
    assert "hunter2" not in rendered
    assert "sk-secret" not in rendered
    assert "full private document body" not in rendered


def test_redact_sensitive_event_masks_known_keys_in_place() -> None:
    event = {
        "message": "ok",
        "password": "x",
        "refresh_token": "y",
        "chunk_text": "private",
        "tokens": 3,
        "operation": "auth.login",
    }
    result = redact_sensitive_event(None, "info", event)
    redacted = "[redacted]"
    assert result["password"] == redacted
    assert result["refresh_token"] == redacted
    assert result["chunk_text"] == redacted
    assert result["tokens"] == 3
    assert result["operation"] == "auth.login"


def test_trace_context_is_added_when_a_span_is_active(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )
    configure_tracing(
        service_name="doculens-test",
        settings=CoreSettings(
            _env_file=None,
            database_url=SecretStr(
                "postgresql+asyncpg://doculens:doculens@localhost:5432/doculens"
            ),
            otel_traces_exporter=OtelTracesExporter.OTLP,
            otel_exporter_otlp_endpoint="http://127.0.0.1:4318",
        ),
        exporter=InMemorySpanExporter(),
    )
    with start_span("unit.test"):
        structlog.get_logger("doculens.test").info("correlated")

    entries = _json_lines(capsys.readouterr().out)
    entry = next(item for item in entries if item.get("message") == "correlated")
    trace_id = entry["trace_id"]
    span_id = entry["span_id"]
    assert isinstance(trace_id, str)
    assert len(trace_id) == 32
    assert isinstance(span_id, str)
    assert len(span_id) == 16
    assert trace_id != "0" * 32
