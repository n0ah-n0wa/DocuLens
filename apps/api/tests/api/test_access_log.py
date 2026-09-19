import json
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from doculens.infrastructure.config import LogLevel
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api


def test_each_request_emits_one_structured_access_log_entry(
    capsys: pytest.CaptureFixture[str], settings: ApiSettings
) -> None:
    app = create_app(settings.model_copy(update={"log_level": LogLevel.INFO}))
    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "log-1"})

    assert response.status_code == HTTPStatus.OK
    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    access = [entry for entry in entries if entry.get("operation") == "http.request"]
    assert len(access) == 1
    entry = access[0]
    assert entry["request_id"] == "log-1"
    assert entry["method"] == "GET"
    assert entry["path"] == "/health/live"
    assert entry["status_code"] == HTTPStatus.OK
    assert entry["service"] == "doculens-api"
    assert entry["environment"] == "local"
    assert isinstance(entry["duration_ms"], float)


def test_the_logging_context_does_not_leak_between_requests(
    capsys: pytest.CaptureFixture[str], settings: ApiSettings
) -> None:
    app = create_app(settings.model_copy(update={"log_level": LogLevel.INFO}))
    with TestClient(app) as client:
        client.get("/health/live", headers={"X-Request-ID": "first"})
        client.get("/health/live")

    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    request_ids = [entry["request_id"] for entry in entries if "request_id" in entry]
    assert request_ids[0] == "first"
    assert request_ids[1] != "first"
