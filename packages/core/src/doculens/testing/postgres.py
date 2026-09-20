"""A PostgreSQL for integration tests: an existing server or a disposable container.

Shared by every package's integration conftest. ``DOCULENS_TEST_DATABASE_URL`` selects an existing
server (whose contents the tests destroy); otherwise a testcontainers PostgreSQL is started.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config

POSTGRES_IMAGE = "postgres:17.11-alpine"
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"

# The Ryuk reaper sidecar cannot map its port on some Docker Desktop hosts (Windows reserved port
# ranges); containers are stopped explicitly instead unless the environment says otherwise.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


class DatabaseUnavailableError(RuntimeError):
    """No PostgreSQL could be provided (typically: Docker is not running)."""


def alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("doculens.database_url", database_url)
    return config


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


@contextmanager
def provisioned_database_url() -> Iterator[str]:
    configured = os.environ.get("DOCULENS_TEST_DATABASE_URL")
    if configured:
        yield configured
        return

    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415 - optional dependency

    container = PostgresContainer(POSTGRES_IMAGE, driver="asyncpg")
    try:
        container.start()
    except Exception as exc:
        message = f"PostgreSQL container unavailable (is Docker running?): {exc}"
        raise DatabaseUnavailableError(message) from exc
    try:
        yield container.get_connection_url()
    finally:
        container.stop()
