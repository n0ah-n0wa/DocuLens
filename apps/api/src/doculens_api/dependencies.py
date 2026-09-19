"""Dependency injection for request handlers.

Everything a handler needs is assembled once by :func:`doculens_api.main.create_app` into an
immutable :class:`AppComponents` stored on that application instance. Handlers receive components
through ``Annotated`` aliases such as :data:`ReadinessDep`, so tests can build isolated
applications with fakes and no module-level state is shared between instances.
"""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from doculens.application.health import ReadinessService
from doculens_api.settings import ApiSettings


@dataclass(frozen=True, slots=True)
class AppComponents:
    settings: ApiSettings
    readiness: ReadinessService


def components_of(request: Request) -> AppComponents:
    return cast("AppComponents", request.app.state.components)


def get_readiness_service(request: Request) -> ReadinessService:
    return components_of(request).readiness


ReadinessDep = Annotated[ReadinessService, Depends(get_readiness_service)]
