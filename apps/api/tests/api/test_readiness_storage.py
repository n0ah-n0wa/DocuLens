"""Object storage in the API: readiness reports it, and handlers only get an owner-scoped view."""

from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from doculens.application.storage import OwnerScopedObjectStorage
from doculens.domain.users import User, UserStatus
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens_api import dependencies
from doculens_api.dependencies import get_owned_object_storage
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.api


def test_ready_reports_object_storage_and_the_database(
    settings: ApiSettings, tmp_path: Path
) -> None:
    local = settings.model_copy(update={"storage_local_root": tmp_path / "objects"})
    app = create_app(local)
    assert isinstance(app.state.components.object_storage, FilesystemObjectStorage)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE  # the database is unreachable
    checks = {check["name"]: check["status"] for check in response.json()["checks"]}
    assert checks == {"postgres": "fail", "object-storage": "pass", "job-queue": "pass"}
    assert (tmp_path / "objects" / "objects").is_dir()


async def test_handlers_only_ever_receive_owner_scoped_storage(settings: ApiSettings) -> None:
    """The dependency binds the raw adapter to the authenticated user (§9, §11)."""
    app = create_app(settings)
    request = Request({"type": "http", "app": app, "headers": [], "method": "GET", "path": "/"})
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        email="alice@example.com",
        password_hash="unused",  # noqa: S106 - not a credential
        status=UserStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )

    scoped = await get_owned_object_storage(request, user)

    assert isinstance(scoped, OwnerScopedObjectStorage)
    assert scoped.owner_id == user.id
    # No dependency exposes the unscoped adapter to request handlers.
    assert not hasattr(dependencies, "ObjectStorageDep")
