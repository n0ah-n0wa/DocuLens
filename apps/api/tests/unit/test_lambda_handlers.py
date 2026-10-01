"""Unit tests for API Lambda and migrate handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from doculens_api import lambda_handler as api_lambda
from doculens_api import migrate_handler

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_api_lambda_handler_reuses_mangum(monkeypatch: pytest.MonkeyPatch) -> None:
    api_lambda._handler = None  # noqa: SLF001 - test isolation
    calls: list[tuple[Any, Any]] = []
    app = object()

    class _FakeMangum:
        def __init__(self, bound_app: object, *, lifespan: str) -> None:
            assert lifespan == "on"
            assert bound_app is app

        def __call__(self, event: dict[str, Any], context: object) -> dict[str, str]:
            calls.append((event, context))
            return {"statusCode": "200"}

    def _create_app() -> object:
        return app

    monkeypatch.setattr(api_lambda, "Mangum", _FakeMangum)
    monkeypatch.setattr(api_lambda, "create_app", _create_app)

    event = {"version": "2.0", "rawPath": "/health/live"}
    context = object()
    first = api_lambda.handler(event, context)
    second = api_lambda.handler(event, context)

    assert first == {"statusCode": "200"}
    assert second == {"statusCode": "200"}
    assert len(calls) == 2
    assert api_lambda._handler is not None  # noqa: SLF001


def test_migrate_handler_runs_alembic_upgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ini = tmp_path / "alembic.ini"
    scripts = tmp_path / "alembic"
    scripts.mkdir()
    ini.write_text("[alembic]\nscript_location = alembic\n", encoding="utf-8")

    class _Settings:
        class _Url:
            @staticmethod
            def get_secret_value() -> str:
                return "postgresql+asyncpg://u:p@db/db"

        database_url = _Url()

    upgrades: list[str] = []

    class _Config:
        def __init__(self, path: str) -> None:
            self.path = path
            self.options: dict[str, str] = {}

        def set_main_option(self, key: str, value: str) -> None:
            self.options[key] = value

    monkeypatch.setattr(migrate_handler, "ALEMBIC_INI", ini)
    monkeypatch.setattr(migrate_handler, "ALEMBIC_ROOT", scripts)
    monkeypatch.setattr(migrate_handler, "load_settings", lambda *_a, **_k: _Settings())
    monkeypatch.setattr(migrate_handler, "Config", _Config)
    monkeypatch.setattr(
        migrate_handler.command,
        "upgrade",
        lambda config, revision: upgrades.append(f"{config.path}:{revision}"),
    )

    result = migrate_handler.handler({}, object())

    assert result == {"status": "ok", "action": "upgrade", "revision": "head"}
    assert upgrades == [f"{ini}:head"]


def test_migrate_handler_requires_alembic_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Settings:
        class _Url:
            @staticmethod
            def get_secret_value() -> str:
                return "postgresql+asyncpg://u:p@db/db"

        database_url = _Url()

    monkeypatch.setattr(migrate_handler, "ALEMBIC_INI", tmp_path / "missing.ini")
    monkeypatch.setattr(migrate_handler, "ALEMBIC_ROOT", tmp_path / "missing")
    monkeypatch.setattr(migrate_handler, "load_settings", lambda *_a, **_k: _Settings())

    with pytest.raises(RuntimeError, match="alembic assets missing"):
        migrate_handler.handler({}, object())
