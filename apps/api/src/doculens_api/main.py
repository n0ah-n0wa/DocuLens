"""FastAPI application factory."""

from fastapi import FastAPI

from doculens_api import __version__
from doculens_api.routers import health


def create_app() -> FastAPI:
    """Build the API application.

    Routers, middleware and dependency overrides are registered here so that tests can construct
    isolated application instances instead of sharing the module-level ``app``.
    """
    app = FastAPI(
        title="DocuLens API",
        version=__version__,
        description="Document intelligence and grounded question answering over PDF collections.",
    )
    app.include_router(health.router)
    return app


app = create_app()
