import json
import logging

import pytest
import structlog

from doculens.infrastructure.config import LogFormat, LogLevel
from doculens.infrastructure.logging import configure_logging

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


def test_exceptions_are_rendered_as_structured_data(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        service="doculens-test", environment="local", level=LogLevel.INFO, log_format=LogFormat.JSON
    )

    message = "boom"
    try:
        raise RuntimeError(message)  # noqa: TRY301 - the point is an exception with a traceback
    except RuntimeError:
        structlog.get_logger("doculens.test").exception("failed")

    (entry,) = _json_lines(capsys.readouterr().out)
    assert entry["level"] == "error"
    assert isinstance(entry["exception"], list)
    assert "RuntimeError" in json.dumps(entry["exception"])


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
