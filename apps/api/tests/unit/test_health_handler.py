import pytest

from doculens.application.health import ProbeResult, ReadinessReport
from doculens_api import __version__
from doculens_api.routers.health import ReadinessResponse, live

pytestmark = pytest.mark.unit


async def test_live_handler_reports_service_identity() -> None:
    response = await live()

    assert response.status == "ok"
    assert response.service == "doculens-api"
    assert response.version == __version__


def test_readiness_response_mirrors_the_report() -> None:
    report = ReadinessReport(
        results=(
            ProbeResult(name="postgres", healthy=True),
            ProbeResult(name="redis", healthy=False, detail="timed out"),
        )
    )

    response = ReadinessResponse.from_report(report)

    assert response.status == "not_ready"
    assert [check.model_dump() for check in response.checks] == [
        {"name": "postgres", "status": "pass", "detail": None},
        {"name": "redis", "status": "fail", "detail": "timed out"},
    ]
