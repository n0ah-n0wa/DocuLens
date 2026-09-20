"""The SQLAlchemy adapters satisfy the application-layer ports structurally (checked by mypy)."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doculens.infrastructure.persistence.repositories import (
    SqlAlchemyCollectionRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyDocumentContentRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyMessageRepository,
    SqlAlchemyUserRepository,
)
from doculens.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from doculens.application.unit_of_work import (
        CollectionRepository,
        ConversationRepository,
        DocumentContentRepository,
        DocumentRepository,
        MessageRepository,
        UnitOfWork,
        UnitOfWorkFactory,
        UserRepository,
    )

pytestmark = pytest.mark.unit


def test_adapters_conform_to_their_ports() -> None:
    session = AsyncSession()
    users: UserRepository = SqlAlchemyUserRepository(session)
    collections: CollectionRepository = SqlAlchemyCollectionRepository(session)
    documents: DocumentRepository = SqlAlchemyDocumentRepository(session)
    content: DocumentContentRepository = SqlAlchemyDocumentContentRepository(session)
    conversations: ConversationRepository = SqlAlchemyConversationRepository(session)
    messages: MessageRepository = SqlAlchemyMessageRepository(session)

    assert all(
        adapter is not None
        for adapter in (users, collections, documents, content, conversations, messages)
    )


def _build_unit_of_work() -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(async_sessionmaker())


async def test_unit_of_work_conforms_to_its_port_and_requires_entering() -> None:
    factory: UnitOfWorkFactory = _build_unit_of_work
    unit_of_work: UnitOfWork = factory()

    with pytest.raises(RuntimeError, match="async with"):
        await unit_of_work.commit()
