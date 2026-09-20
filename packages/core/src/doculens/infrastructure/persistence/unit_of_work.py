"""One SQLAlchemy session per unit of work; explicit commit (SPECIFICATIONS.md §46)."""

import contextlib
from types import TracebackType
from typing import Self

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doculens.domain.errors import DatabaseUnavailableError
from doculens.infrastructure.persistence.repositories import (
    SqlAlchemyCollectionRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyDocumentContentRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyMessageRepository,
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)


def _is_connection_failure(exc: BaseException) -> bool:
    if isinstance(exc, (OperationalError, InterfaceError, ConnectionError, TimeoutError, OSError)):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


class SqlAlchemyUnitOfWork:
    users: SqlAlchemyUserRepository
    refresh_tokens: SqlAlchemyRefreshTokenRepository
    collections: SqlAlchemyCollectionRepository
    documents: SqlAlchemyDocumentRepository
    document_content: SqlAlchemyDocumentContentRepository
    conversations: SqlAlchemyConversationRepository
    messages: SqlAlchemyMessageRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        session = self._session_factory()
        self._session = session
        self.users = SqlAlchemyUserRepository(session)
        self.refresh_tokens = SqlAlchemyRefreshTokenRepository(session)
        self.collections = SqlAlchemyCollectionRepository(session)
        self.documents = SqlAlchemyDocumentRepository(session)
        self.document_content = SqlAlchemyDocumentContentRepository(session)
        self.conversations = SqlAlchemyConversationRepository(session)
        self.messages = SqlAlchemyMessageRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._require_session()
        try:
            # Anything not committed explicitly is discarded, whether or not an error occurred;
            # a dead connection cannot roll back and closing it is enough.
            with contextlib.suppress(Exception):
                await session.rollback()
        finally:
            await session.close()
            self._session = None
        if exc is not None and _is_connection_failure(exc):
            # Use cases see one retryable error instead of driver-specific exceptions (§67).
            raise DatabaseUnavailableError from exc

    async def commit(self) -> None:
        try:
            await self._require_session().commit()
        except Exception as exc:
            if _is_connection_failure(exc):
                raise DatabaseUnavailableError from exc
            raise

    async def rollback(self) -> None:
        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            message = "the unit of work must be entered with 'async with' before use"
            raise RuntimeError(message)
        return self._session
