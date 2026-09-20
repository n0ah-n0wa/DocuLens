"""Integration-test infrastructure: a real PostgreSQL, migrated with Alembic.

Without Docker the suite is skipped locally and fails in CI, so the gate is never silently green.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from doculens.infrastructure.config import CoreSettings
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.persistence.models import Base
from doculens.testing.factories import Factories
from doculens.testing.postgres import (
    DatabaseUnavailableError,
    provisioned_database_url,
    upgrade_to_head,
)
from doculens.testing.postgres import (
    alembic_config as _alembic_config,
)

AlembicConfigFactory = Callable[[str], Config]


@pytest.fixture(scope="session")
def alembic_config() -> AlembicConfigFactory:
    return _alembic_config


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    try:
        with provisioned_database_url() as url:
            yield url
    except DatabaseUnavailableError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def migration_database_url(database_url: str) -> str:
    """A second database on the same server, so migration tests never touch the shared schema."""
    name = "doculens_migration_tests"
    admin_url = make_url(database_url)
    target_url = admin_url.set(database=name)

    async def recreate() -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            await engine.dispose()

    asyncio.run(recreate())
    return target_url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    upgrade_to_head(database_url)
    return database_url


@pytest.fixture
def settings(migrated_database_url: str) -> CoreSettings:
    return CoreSettings(_env_file=None, database_url=SecretStr(migrated_database_url))


@pytest.fixture
async def database(settings: CoreSettings) -> AsyncIterator[Database]:
    db = Database(settings)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(database: Database) -> None:
    """Every test starts from empty tables; the schema itself is kept."""
    tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    async with database.session_factory() as session:
        await session.execute(text(f"TRUNCATE {tables} CASCADE"))
        await session.commit()


@pytest.fixture
def factories() -> type[Factories]:
    return Factories
