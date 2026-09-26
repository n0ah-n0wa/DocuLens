from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from doculens_api import __version__
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api


class FailingProbe:
    name = "postgres"

    async def check(self) -> None:
        message = "unreachable"
        raise ConnectionError(message)


class HealthyProbe:
    name = "redis"

    async def check(self) -> None:
        return None


def test_liveness_reports_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok", "service": "doculens-api", "version": __version__}


def test_readiness_is_ready_when_every_probe_passes(settings: ApiSettings) -> None:
    with TestClient(create_app(settings, probes=[HealthyProbe()])) as client:
        response = client.get("/health/ready")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "status": "ready",
        "checks": [{"name": "redis", "status": "pass", "detail": None}],
    }


def test_readiness_checks_the_real_database_by_default(settings: ApiSettings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    checks = {check["name"]: check for check in response.json()["checks"]}
    assert set(checks) == {"postgres", "object-storage", "job-queue"}
    assert checks["postgres"]["status"] == "fail"
    assert checks["postgres"]["detail"] in {"failed", "timed out"}


def test_readiness_answers_503_while_a_dependency_fails(settings: ApiSettings) -> None:
    with TestClient(create_app(settings, probes=[HealthyProbe(), FailingProbe()])) as client:
        response = client.get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    body = response.json()
    assert body["status"] == "not_ready"
    assert {"name": "postgres", "status": "fail", "detail": "failed"} in body["checks"]
    assert "unreachable" not in response.text
