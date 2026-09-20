import pytest
from pydantic import SecretStr

from doculens_api.dependencies import AppComponents
from doculens_api.main import create_app
from doculens_api.settings import ApiSettings

pytestmark = pytest.mark.unit


def test_each_application_instance_owns_its_components(settings: ApiSettings) -> None:
    first = create_app(settings)
    second = create_app(settings.model_copy(update={"request_id_header": "X-Other"}))

    assert isinstance(first.state.components, AppComponents)
    assert first.state.components is not second.state.components
    assert first.state.components.settings.request_id_header == "X-Request-ID"
    assert second.state.components.settings.request_id_header == "X-Other"


def test_default_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_DOCS_ENABLED", "false")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/doculens")

    app = create_app()

    assert app.state.components.settings.api_docs_enabled is False
    assert app.openapi_url is None


def test_request_id_header_name_is_validated(settings: ApiSettings) -> None:
    with pytest.raises(ValueError, match="request_id_header"):
        ApiSettings(
            _env_file=None, database_url=settings.database_url, request_id_header="not a header"
        )


def test_settings_never_reveal_the_database_password(settings: ApiSettings) -> None:
    assert "not-a-secret" not in repr(settings)
    assert "not-a-secret" not in str(settings.database_url)
    assert "not-a-secret" in settings.database_url.get_secret_value()


def test_the_database_url_must_use_the_async_postgres_driver() -> None:
    with pytest.raises(ValueError, match="postgresql\\+asyncpg"):
        ApiSettings(_env_file=None, database_url=SecretStr("postgresql://u:p@127.0.0.1:1/doculens"))
