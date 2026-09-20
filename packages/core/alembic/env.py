"""Alembic environment: migrations run against the ORM metadata over the async engine.

The database URL is resolved in this order: the ``doculens.database_url`` main option (set
programmatically by tests), the ``-x database_url=...`` command-line argument, then the process
settings (``DATABASE_URL``). Alembic is the only way the schema changes (§45).
"""

import asyncio
import concurrent.futures
from collections.abc import Coroutine

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from doculens.infrastructure.config import CoreSettings, load_settings
from doculens.infrastructure.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    configured = config.get_main_option("doculens.database_url")
    if configured:
        return configured
    override = context.get_x_argument(as_dictionary=True).get("database_url")
    if override:
        return override
    return load_settings(CoreSettings).database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations_with_connection)
    finally:
        await engine.dispose()


def run_coroutine(coroutine: Coroutine[object, object, None]) -> None:
    """Run the migration coroutine whether or not an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coroutine)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, coroutine).result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_coroutine(run_async_migrations())
