"""Platform probes.

``/health/live`` answers as soon as the process serves requests. ``/health/ready`` runs the
registered dependency probes and answers 503 while any of them fails, so load balancers and
orchestrators stop routing traffic to an instance whose dependencies are unusable.
"""

from http import HTTPStatus
from typing import Literal, Self

from fastapi import APIRouter, Response
from pydantic import BaseModel

from doculens.application.health import ReadinessReport
from doculens_api import SERVICE_NAME, __version__
from doculens_api.dependencies import ReadinessDep

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    """Reported when the process is up. It does not imply that dependencies are reachable."""

    status: Literal["ok"]
    service: Literal["doculens-api"]
    version: str


class CheckResult(BaseModel):
    name: str
    status: Literal["pass", "fail"]
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: list[CheckResult]

    @classmethod
    def from_report(cls, report: ReadinessReport) -> Self:
        return cls(
            status="ready" if report.ready else "not_ready",
            checks=[
                CheckResult(
                    name=result.name,
                    status="pass" if result.healthy else "fail",
                    detail=result.detail,
                )
                for result in report.results
            ],
        )


@router.get("/live", summary="Liveness probe")
async def live() -> LivenessResponse:
    return LivenessResponse(status="ok", service=SERVICE_NAME, version=__version__)


@router.get(
    "/ready",
    summary="Readiness probe",
    responses={HTTPStatus.SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(readiness: ReadinessDep, response: Response) -> ReadinessResponse:
    report = await readiness.check()
    if not report.ready:
        response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    return ReadinessResponse.from_report(report)
