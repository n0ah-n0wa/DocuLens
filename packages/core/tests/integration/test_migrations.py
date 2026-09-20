"""Migration correctness: the migration chain and the ORM metadata describe the same schema."""

import asyncio
from collections.abc import Callable

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, pool
from sqlalchemy.ext.asyncio import create_async_engine

from doculens.infrastructure.persistence.models import Base

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users",
    "refresh_tokens",
    "collections",
    "documents",
    "document_pages",
    "document_chunks",
    "conversations",
    "messages",
    "citations",
}


def _schema_diff(connection: Connection) -> list[object]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, Base.metadata))


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


async def _with_connection[T](database_url: str, fn: Callable[[Connection], T]) -> T:
    engine = create_async_engine(database_url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(fn)
    finally:
        await engine.dispose()


def test_the_migration_chain_has_exactly_one_head(
    migration_database_url: str, alembic_config: Callable[[str], Config]
) -> None:
    script = ScriptDirectory.from_config(alembic_config(migration_database_url))

    assert len(script.get_heads()) == 1


def test_upgrading_from_an_empty_database_creates_every_table(
    migration_database_url: str, alembic_config: Callable[[str], Config]
) -> None:
    config = alembic_config(migration_database_url)
    command.downgrade(config, "base")
    assert asyncio.run(_with_connection(migration_database_url, _table_names)) == {
        "alembic_version"
    }

    command.upgrade(config, "head")

    assert asyncio.run(
        _with_connection(migration_database_url, _table_names)
    ) == EXPECTED_TABLES | {"alembic_version"}


def test_migrations_and_models_agree(
    migration_database_url: str, alembic_config: Callable[[str], Config]
) -> None:
    command.upgrade(alembic_config(migration_database_url), "head")
    diff = asyncio.run(_with_connection(migration_database_url, _schema_diff))

    assert diff == [], f"migrations drift from the ORM models: {diff}"


def test_downgrade_then_upgrade_is_repeatable(
    migration_database_url: str, alembic_config: Callable[[str], Config]
) -> None:
    config = alembic_config(migration_database_url)

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    assert asyncio.run(_with_connection(migration_database_url, _schema_diff)) == []
