"""One SQLAlchemy session per unit of work; explicit commit (SPECIFICATIONS.md §46)."""

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doculens.infrastructure.persistence.repositories import (
    SqlAlchemyCollectionRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyDocumentContentRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyMessageRepository,
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyUserRepository,
)


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
            # Anything not committed explicitly is discarded, whether or not an error occurred.
            await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        await self._require_session().commit()

    async def rollback(self) -> None:
        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            message = "the unit of work must be entered with 'async with' before use"
            raise RuntimeError(message)
        return self._session
