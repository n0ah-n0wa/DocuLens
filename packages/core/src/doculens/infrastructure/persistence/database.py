"""Engine and session lifecycle for one process.

``Database`` owns the async engine (created lazily, no connection until first use) and hands out
units of work. Composition roots create one instance at start-up and dispose of it at shutdown.
The pool is deliberately small; connection multiplexing for Lambda is ADR-012.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from doculens.infrastructure.config import CoreSettings
from doculens.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork


class Database:
    def __init__(self, settings: CoreSettings) -> None:
        self._engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
            pool_pre_ping=True,
            echo=settings.database_echo,
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)

    async def ping(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self._engine.dispose()


class DatabaseProbe:
    """Readiness probe for ``/health/ready``: the database answers a trivial query."""

    name = "postgres"

    def __init__(self, database: Database) -> None:
        self._database = database

    async def check(self) -> None:
        await self._database.ping()
