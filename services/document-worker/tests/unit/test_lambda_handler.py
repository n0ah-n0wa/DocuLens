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


def test_handler_reports_batch_item_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        app_env=Environment.LOCAL,
        log_format=LogFormat.JSON,
        log_level=LogLevel.INFO,
    )
    monkeypatch.setattr(worker_lambda, "_settings", lambda: settings)

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
            }
        ]
    }
    result = worker_lambda.handler(event, object())
    assert result == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}


def test_handler_succeeds_for_processed_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = CoreSettings(
        _env_file=None,
        database_url=DB_URL,
        app_env=Environment.LOCAL,
        log_format=LogFormat.JSON,
        log_level=LogLevel.INFO,
    )
    monkeypatch.setattr(worker_lambda, "_settings", lambda: settings)

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
