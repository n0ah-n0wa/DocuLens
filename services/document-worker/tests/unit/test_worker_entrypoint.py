import json

import pytest

from doculens_worker import __version__
from doculens_worker.entrypoint import EXIT_CONFIGURATION_ERROR, main

pytestmark = pytest.mark.unit


def test_main_exits_cleanly_and_logs_a_structured_start_event(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("APP_ENV", "local")

    exit_code = main()

    assert exit_code == 0
    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    (start,) = [entry for entry in entries if entry.get("operation") == "worker.start"]
    assert start["service"] == "doculens-worker"
    assert start["version"] == __version__
    assert start["handlers_registered"] == 0


def test_invalid_configuration_fails_the_process_with_a_clear_message(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_FORMAT", "console")

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == EXIT_CONFIGURATION_ERROR
    assert "doculens-worker: invalid configuration" in captured.err
    assert "LOG_FORMAT must be json" in captured.err
    assert "Traceback" not in captured.err
