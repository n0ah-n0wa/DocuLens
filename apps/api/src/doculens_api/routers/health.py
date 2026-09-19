"""Liveness probe.

A readiness probe that checks PostgreSQL, Redis and the vector store is added together with the
first infrastructure adapters.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from doculens_api import __version__

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    """Reported when the process is up. It does not imply that dependencies are reachable."""

    status: Literal["ok"]
    service: Literal["doculens-api"]
    version: str


@router.get("/live", summary="Liveness probe")
async def live() -> LivenessResponse:
    return LivenessResponse(status="ok", service="doculens-api", version=__version__)
