import pytest

from doculens_api import __version__
from doculens_api.routers.health import live

pytestmark = pytest.mark.unit


async def test_live_handler_reports_service_identity() -> None:
    response = await live()

    assert response.status == "ok"
    assert response.service == "doculens-api"
    assert response.version == __version__
