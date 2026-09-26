import asyncio
import json
from uuid import UUID, uuid4

import pytest

from doculens.application.ingestion import ProcessingOutcome, ProcessingReport
from doculens.domain.documents import ProcessingStatus
from doculens.infrastructure.config import CoreSettings
from doculens_worker import __version__, entrypoint
from doculens_worker.entrypoint import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_PROCESSING_FAILED,
    EXIT_USAGE_ERROR,
    main,
)

pytestmark = pytest.mark.unit


def test_main_exits_cleanly_and_logs_a_structured_start_event(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/doculens")

    async def fake_run(
        settings: CoreSettings,
        *,
        stop: asyncio.Event | None = None,
        max_idle_polls: int | None = None,
    ) -> None:
        del settings, stop, max_idle_polls

    monkeypatch.setattr(entrypoint, "run_worker", fake_run)

    exit_code = main([])

    assert exit_code == 0
    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    (start,) = [entry for entry in entries if entry.get("operation") == "worker.start"]
    assert start["service"] == "doculens-worker"
    assert start["version"] == __version__
    assert start["handlers_registered"] == 1
    assert start["queue_backend"] == "memory"


def test_invalid_configuration_fails_the_process_with_a_clear_message(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_FORMAT", "console")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/doculens")

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == EXIT_CONFIGURATION_ERROR
    assert "doculens-worker: invalid configuration" in captured.err
    assert "LOG_FORMAT must be json" in captured.err
    assert "Traceback" not in captured.err


def test_unknown_arguments_print_usage_and_exit_with_a_usage_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["process"]) == EXIT_USAGE_ERROR
    assert main(["process", "not-a-uuid"]) == EXIT_USAGE_ERROR
    assert main(["frobnicate"]) == EXIT_USAGE_ERROR
    assert "usage:" in capsys.readouterr().err


def test_process_runs_one_document_and_maps_the_outcome_to_an_exit_code(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/doculens")
    document_id = uuid4()
    outcomes = iter([ProcessingOutcome.PROCESSED, ProcessingOutcome.FAILED])

    async def fake_process(settings: CoreSettings, requested: UUID) -> ProcessingReport:
        del settings
        assert requested == document_id
        return ProcessingReport(requested, next(outcomes), ProcessingStatus.CHUNKING)

    monkeypatch.setattr(entrypoint, "process_document", fake_process)

    assert main(["process", str(document_id)]) == 0
    assert main(["process", str(document_id)]) == EXIT_PROCESSING_FAILED
    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    processed = [e for e in entries if e.get("operation") == "worker.process"]
    assert [e["outcome"] for e in processed] == ["processed", "failed"]


def test_an_infrastructure_failure_during_process_is_reported_not_raised(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/doculens")

    async def broken(settings: CoreSettings, requested: UUID) -> None:
        del settings, requested
        message = "database unreachable"
        raise OSError(message)

    monkeypatch.setattr(entrypoint, "process_document", broken)

    assert main(["process", str(uuid4())]) == EXIT_PROCESSING_FAILED
    output = capsys.readouterr().out
    assert "document processing aborted" in output
