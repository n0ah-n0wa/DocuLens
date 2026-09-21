"""Conversations and messages in PostgreSQL through the use cases (§9, §28): create, list order,
rename, delete with its cascade, and strict ownership at the database."""

import pytest

from doculens.application.conversations import ConversationService
from doculens.domain.conversations import (
    Citation,
    Conversation,
    ConversationNotFoundError,
    Message,
    MessageRole,
)
from doculens.domain.documents import Document
from doculens.domain.errors import ConflictError
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.domain.users import User
from doculens.infrastructure.persistence.database import Database
from doculens.testing.factories import Factories

pytestmark = pytest.mark.integration


@pytest.fixture
async def service(database: Database) -> ConversationService:
    return ConversationService(unit_of_work=database.unit_of_work)


async def test_conversations_list_most_recently_updated_first(
    database: Database, service: ConversationService, factories: type[Factories]
) -> None:
    owner = factories.user()
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.commit()
    first = await service.create(owner.id, title="First", collection_id=None)
    second = await service.create(owner.id, title="Second", collection_id=None)
    assert [c.id for c in await service.list_for_owner(owner.id)] == [second.id, first.id]

    renamed = await service.update(owner.id, first.id, title="First, renamed")

    assert renamed.title == "First, renamed"
    assert renamed.updated_at > first.updated_at
    assert [c.id for c in await service.list_for_owner(owner.id)] == [first.id, second.id]
    assert (await service.get(owner.id, first.id)).title == "First, renamed"


async def test_deleting_a_conversation_cascades_to_its_messages_and_citations(
    database: Database, service: ConversationService, factories: type[Factories]
) -> None:
    owner = factories.user()
    document = factories.document(owner.id)
    conversation = await _seeded_conversation(database, service, owner, document)

    await service.delete(owner.id, conversation.id)

    with pytest.raises(ConversationNotFoundError):
        await service.get(owner.id, conversation.id)
    with pytest.raises(ConversationNotFoundError):
        await service.list_messages(owner.id, conversation.id)
    async with database.unit_of_work() as uow:
        assert await uow.messages.list_for_conversation(owner.id, conversation.id) == []
        assert await uow.messages.list_citations_for_conversation(owner.id, conversation.id) == {}
        assert await uow.documents.get(owner.id, document.id) is not None  # documents survive


async def test_another_user_cannot_see_rename_delete_or_read_a_conversation(
    database: Database, service: ConversationService, factories: type[Factories]
) -> None:
    owner, other = factories.user("owner@example.com"), factories.user("other@example.com")
    document = factories.document(owner.id)
    async with database.unit_of_work() as uow:
        await uow.users.add(other)
        await uow.commit()
    conversation = await _seeded_conversation(database, service, owner, document)

    assert await service.list_for_owner(other.id) == []
    with pytest.raises(ConversationNotFoundError):
        await service.get(other.id, conversation.id)
    with pytest.raises(ConversationNotFoundError):
        await service.update(other.id, conversation.id, title="Taken")
    with pytest.raises(ConversationNotFoundError):
        await service.delete(other.id, conversation.id)
    with pytest.raises(ConversationNotFoundError):
        await service.list_messages(other.id, conversation.id)
    # Nothing changed for the owner.
    history = await service.list_messages(owner.id, conversation.id)
    assert [m.message.role for m in history] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert len(history[1].citations) == 1
    assert (await service.get(owner.id, conversation.id)).title == "Risks"


async def _seeded_conversation(
    database: Database, service: ConversationService, owner: User, document: Document
) -> Conversation:
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.documents.add(document)
        await uow.commit()
    conversation = await service.create(owner.id, title="Risks", collection_id=None)
    question = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="What are the risks?",
        created_at=utc_now(),
    )
    answer = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Three risks [1].",
        created_at=utc_now(),
    )
    citation = Citation(
        id=new_id(),
        message_id=answer.id,
        document_id=document.id,
        page_number=3,
        quoted_text="three risks",
        retrieval_score=0.8,
        citation_order=0,
    )
    async with database.unit_of_work() as uow:
        await uow.messages.add(owner.id, question)
        await uow.messages.add(owner.id, answer, [citation])
        await uow.commit()
    return conversation


async def test_listing_can_be_limited_to_the_newest_messages(
    database: Database, service: ConversationService, factories: type[Factories]
) -> None:
    owner = factories.user()
    document = factories.document(owner.id)
    conversation = await _seeded_conversation(database, service, owner, document)
    extra = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="And the mitigations?",
        created_at=utc_now(),
    )
    async with database.unit_of_work() as uow:
        await uow.messages.add(owner.id, extra)
        await uow.commit()

    async with database.unit_of_work() as uow:
        everything = await uow.messages.list_for_conversation(owner.id, conversation.id)
        newest_two = await uow.messages.list_for_conversation(owner.id, conversation.id, limit=2)
        none = await uow.messages.list_for_conversation(owner.id, conversation.id, limit=0)
        foreign = await uow.messages.list_for_conversation(new_id(), conversation.id, limit=2)

    assert [m.content for m in everything] == [
        "What are the risks?",
        "Three risks [1].",
        "And the mitigations?",
    ]
    assert [m.content for m in newest_two] == ["Three risks [1].", "And the mitigations?"]
    assert none == []
    assert foreign == []


async def test_a_citation_to_a_vanished_source_is_a_conflict_not_a_crash(
    database: Database, service: ConversationService, factories: type[Factories]
) -> None:
    owner = factories.user()
    document = factories.document(owner.id)
    conversation = await _seeded_conversation(database, service, owner, document)
    answer = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Cites a chunk that was re-indexed away [1].",
        created_at=utc_now(),
    )
    dangling = Citation(
        id=new_id(),
        message_id=answer.id,
        document_id=document.id,
        page_number=1,
        quoted_text="gone",
        retrieval_score=0.5,
        citation_order=0,
        chunk_id=new_id(),  # no such chunk
    )

    async def persist() -> None:
        async with database.unit_of_work() as uow:
            await uow.messages.add(owner.id, answer, [dangling])
            await uow.commit()

    with pytest.raises(ConflictError):
        await persist()

    history = await service.list_messages(owner.id, conversation.id)
    assert len(history) == 2  # the failed exchange left nothing behind
