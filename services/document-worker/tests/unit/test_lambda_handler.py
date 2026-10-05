"""Unit tests for the SQS Lambda worker handler."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from doculens.application.ingestion import ProcessingOutcome, ProcessingReport
from doculens.domain.documents import ProcessingStatus
from doculens.infrastructure.config import CoreSettings, Environment, LogFormat, LogLevel
from doculens_worker import lambda_handler as worker_lambda

pytestmark = pytest.mark.unit

DB_URL = SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens")


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_lambda._SETTINGS = None  # noqa: SLF001 - test isolation
    monkeypatch.setenv("DATABASE_URL", DB_URL.get_secret_value())
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("LOG_FORMAT", "json")


def _settings(**overrides: object) -> CoreSettings:
    return CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        app_env=Environment.LOCAL,
        log_format=LogFormat.JSON,
        log_level=LogLevel.INFO,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _skip_reconcile(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_settings: CoreSettings) -> None:
        return None

    monkeypatch.setattr(worker_lambda, "_reconcile_stragglers", _noop)


def test_handler_reports_batch_item_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_lambda, "_settings", _settings)

    async def boom(_settings: CoreSettings, _document_id: UUID) -> ProcessingReport:
        message = "boom"
        raise RuntimeError(message)

    monkeypatch.setattr(worker_lambda, "process_document", boom)

    document_id = uuid4()
    event = {
        "Records": [
            {
                "messageId": "m-1",
                "body": json.dumps({"document_id": str(document_id), "job_id": "j1", "attempt": 1}),
                "attributes": {"ApproximateReceiveCount": "1"},
            }
        ]
    }
    result = worker_lambda.handler(event, object())
    assert result == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}


def test_handler_acks_failed_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_lambda, "_settings", _settings)

    async def failed(_settings: CoreSettings, document_id: UUID) -> ProcessingReport:
        return ProcessingReport(
            document_id=document_id,
            outcome=ProcessingOutcome.FAILED,
            status=ProcessingStatus.FAILED,
            stages=(),
        )

    monkeypatch.setattr(worker_lambda, "process_document", failed)
    document_id = uuid4()
    event = {
        "Records": [
            {
                "messageId": "m-fail",
                "body": json.dumps({"document_id": str(document_id)}),
                "attributes": {"ApproximateReceiveCount": "2"},
            }
        ]
    }
    result = worker_lambda.handler(event, object())
    assert result == {"batchItemFailures": []}


def test_handler_abandons_after_receive_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(queue_max_attempts=3)
    monkeypatch.setattr(worker_lambda, "_settings", lambda: settings)
    abandoned: list[UUID] = []

    async def boom(_settings: CoreSettings, _document_id: UUID) -> ProcessingReport:
        message = "still broken"
        raise RuntimeError(message)

    async def abandon(_settings: CoreSettings, document_id: UUID, *, reason: str) -> None:
        del _settings, reason
        abandoned.append(document_id)

    monkeypatch.setattr(worker_lambda, "process_document", boom)
    monkeypatch.setattr(worker_lambda, "_abandon_document", abandon)

    document_id = uuid4()
    event = {
        "Records": [
            {
                "messageId": "m-exhaust",
                "body": json.dumps({"document_id": str(document_id)}),
                "attributes": {"ApproximateReceiveCount": "3"},
            }
        ]
    }
    result = worker_lambda.handler(event, object())
    assert result == {"batchItemFailures": []}
    assert abandoned == [document_id]


def test_handler_succeeds_for_processed_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_lambda, "_settings", _settings)

    async def ok(_settings: CoreSettings, document_id: UUID) -> ProcessingReport:
        return ProcessingReport(
            document_id=document_id,
            outcome=ProcessingOutcome.PROCESSED,
            status=ProcessingStatus.READY,
            stages=(),
        )

    monkeypatch.setattr(worker_lambda, "process_document", ok)
    document_id = uuid4()
    event = {
        "Records": [
            {
                "messageId": "m-2",
                "body": json.dumps({"document_id": str(document_id), "job_id": "j2", "attempt": 1}),
            }
        ]
    }
    result = worker_lambda.handler(event, object())
    assert result == {"batchItemFailures": []}
