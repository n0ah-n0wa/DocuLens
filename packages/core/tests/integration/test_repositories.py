"""Repositories persist domain entities, enforce ownership and respect transaction boundaries."""

from datetime import timedelta

import pytest
from pydantic import SecretStr

from doculens.domain.collections import Collection
from doculens.domain.conversations import Citation, Message, MessageRole
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.errors import DatabaseUnavailableError, NotFoundError
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.domain.users import User, UserStatus
from doculens.infrastructure.config import CoreSettings
from doculens.infrastructure.persistence.database import Database, DatabaseProbe
from doculens.testing.factories import Factories

pytestmark = pytest.mark.integration

UUID_VERSION_4 = 4


async def test_user_can_be_added_read_and_updated(
    database: Database, factories: type[Factories]
) -> None:
    user = factories.user()
    async with database.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.commit()

    async with database.unit_of_work() as uow:
        assert await uow.users.get(user.id) == user
        assert await uow.users.get_by_email(user.email) == user
        assert await uow.users.get_by_email("nobody@example.com") is None

        logged_in = User(
            id=user.id,
            email=user.email,
            password_hash=user.password_hash,
            status=UserStatus.ACTIVE,
            created_at=user.created_at,
            updated_at=user.updated_at + timedelta(seconds=1),
            last_login_at=utc_now(),
        )
        await uow.users.update(logged_in)
        await uow.commit()

    async with database.unit_of_work() as uow:
        stored = await uow.users.get(user.id)
        assert stored is not None
        assert stored.last_login_at is not None


async def test_uncommitted_work_is_rolled_back_when_the_unit_of_work_exits(
    database: Database, factories: type[Factories]
) -> None:
    user = factories.user()
    async with database.unit_of_work() as uow:
        await uow.users.add(user)

    async with database.unit_of_work() as uow:
        assert await uow.users.get(user.id) is None


async def test_an_error_inside_the_unit_of_work_discards_the_transaction(
    database: Database, factories: type[Factories]
) -> None:
    user = factories.user()

    async def add_then_fail() -> None:
        async with database.unit_of_work() as uow:
            await uow.users.add(user)
            message = "boom"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="boom"):
        await add_then_fail()

    async with database.unit_of_work() as uow:
        assert await uow.users.get(user.id) is None


async def test_collections_and_documents_are_scoped_to_their_owner(
    database: Database, factories: type[Factories]
) -> None:
    owner, other = factories.user("owner@example.com"), factories.user("other@example.com")
    collection = factories.collection(owner.id)
    document = factories.document(owner.id, collection.id)
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.users.add(other)
        await uow.collections.add(collection)
        await uow.documents.add(document)
        await uow.commit()

    async with database.unit_of_work() as uow:
        assert await uow.collections.get(owner.id, collection.id) == collection
        assert await uow.collections.get(other.id, collection.id) is None
        assert await uow.documents.get(owner.id, document.id) == document
        assert await uow.documents.get(other.id, document.id) is None
        assert await uow.documents.list_for_owner(other.id) == []
        assert await uow.documents.list_in_collection(owner.id, collection.id) == [document]
        assert await uow.documents.count_for_owner(owner.id) == 1
        assert await uow.documents.count_for_owner(other.id) == 0
        assert await uow.collections.delete(other.id, collection.id) is False
        assert await uow.collections.list_for_owner(owner.id) == [collection]


async def test_updating_a_document_persists_the_state_transition(
    database: Database, factories: type[Factories]
) -> None:
    owner = factories.user()
    document = factories.document(owner.id)
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.documents.add(document)
        await uow.commit()

    validating = document.transition_to(ProcessingStatus.VALIDATING, now=utc_now())
    async with database.unit_of_work() as uow:
        await uow.documents.update(validating)
        await uow.commit()

    async with database.unit_of_work() as uow:
        stored = await uow.documents.get(owner.id, document.id)
        assert stored is not None
        assert stored.processing_status is ProcessingStatus.VALIDATING


async def test_updating_a_document_of_another_owner_is_refused(
    database: Database, factories: type[Factories]
) -> None:
    owner, other = factories.user("owner@example.com"), factories.user("other@example.com")
    document = factories.document(owner.id)
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.users.add(other)
        await uow.documents.add(document)
        await uow.commit()

    forged = Document(
        id=document.id,
        owner_id=other.id,
        collection_id=None,
        filename=document.filename,
        storage_key=document.storage_key,
        content_hash=document.content_hash,
        mime_type=document.mime_type,
        file_size=document.file_size,
        processing_status=document.processing_status,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )
    async with database.unit_of_work() as uow:
        with pytest.raises(NotFoundError):
            await uow.documents.update(forged)


async def test_pages_and_chunks_are_stored_and_listed_in_order(
    database: Database, factories: type[Factories]
) -> None:
    owner = factories.user()
    document = factories.document(owner.id)
    pages = [
        DocumentPage(
            id=new_id(),
            document_id=document.id,
            page_number=number,
            extracted_text=f"page {number}",
            character_count=6,
            metadata={"has_text": True},
        )
        for number in (2, 1)
    ]
    chunks = [
        DocumentChunk(
            id=new_id(),
            document_id=document.id,
            page_id=pages[1].id,
            chunk_index=index,
            text=f"chunk {index}",
            token_count=2,
            metadata={"embedding_model": "fake"},
            vector_id=f"vec-{index}",
        )
        for index in (1, 0)
    ]
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.documents.add(document)
        await uow.document_content.add_pages(pages)
        await uow.document_content.add_chunks(chunks)
        await uow.commit()

    async with database.unit_of_work() as uow:
        stored_pages = await uow.document_content.list_pages(owner.id, document.id)
        assert [page.page_number for page in stored_pages] == [1, 2]
        listed = await uow.document_content.list_chunks(owner.id, document.id)
        assert [chunk.chunk_index for chunk in listed] == [0, 1]
        assert listed[0].metadata == {"embedding_model": "fake"}

        await uow.document_content.delete_content(document.id)
        await uow.commit()

    async with database.unit_of_work() as uow:
        assert await uow.document_content.list_pages(owner.id, document.id) == []
        assert await uow.document_content.list_chunks(owner.id, document.id) == []
        assert await uow.documents.get(owner.id, document.id) is not None


async def test_conversations_messages_and_citations(
    database: Database, factories: type[Factories]
) -> None:
    owner = factories.user()
    document = factories.document(owner.id)
    conversation = factories.conversation(owner.id)
    question = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="What are the main risks?",
        created_at=utc_now(),
    )
    answer = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="The report identifies three main risks.",
        created_at=utc_now() + timedelta(milliseconds=1),
    )
    citations = [
        Citation(
            id=new_id(),
            message_id=answer.id,
            document_id=document.id,
            page_number=17,
            quoted_text="three main risks",
            retrieval_score=0.87,
            citation_order=order,
        )
        for order in (1, 0)
    ]
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.documents.add(document)
        await uow.conversations.add(conversation)
        await uow.messages.add(owner.id, question)
        await uow.messages.add(owner.id, answer, citations)
        await uow.commit()

    async with database.unit_of_work() as uow:
        assert await uow.conversations.list_for_owner(owner.id) == [conversation]
        assert await uow.messages.list_for_conversation(owner.id, conversation.id) == [
            question,
            answer,
        ]
        grouped = await uow.messages.list_citations_for_conversation(owner.id, conversation.id)
        assert [c.citation_order for c in grouped[answer.id]] == [0, 1]
        assert question.id not in grouped

        assert await uow.conversations.delete(owner.id, conversation.id) is True
        await uow.commit()

    async with database.unit_of_work() as uow:
        assert await uow.messages.list_for_conversation(owner.id, conversation.id) == []
        assert await uow.messages.list_citations_for_conversation(owner.id, conversation.id) == {}


async def test_database_probe_passes_against_a_live_database(database: Database) -> None:
    await DatabaseProbe(database).check()


async def test_database_probe_fails_when_the_database_is_unreachable() -> None:
    unreachable = Database(
        CoreSettings(
            _env_file=None,
            database_url=SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens"),
        )
    )
    try:
        with pytest.raises(OSError, match="onnect"):
            await DatabaseProbe(unreachable).check()
    finally:
        await unreachable.dispose()


async def test_identifiers_are_random_version_4_uuids(
    database: Database, factories: type[Factories]
) -> None:
    users = [factories.user(f"user{i}@example.com") for i in range(3)]
    async with database.unit_of_work() as uow:
        for user in users:
            await uow.users.add(user)
        await uow.commit()

    async with database.unit_of_work() as uow:
        stored = [await uow.users.get(user.id) for user in users]

    assert all(user is not None and user.id.version == UUID_VERSION_4 for user in stored)


async def test_messages_and_content_are_invisible_to_other_owners(
    database: Database, factories: type[Factories]
) -> None:
    owner, other = factories.user("owner@example.com"), factories.user("other@example.com")
    document = factories.document(owner.id)
    conversation = factories.conversation(owner.id)
    page = DocumentPage(
        id=new_id(),
        document_id=document.id,
        page_number=1,
        extracted_text="private",
        character_count=7,
        metadata={},
    )
    message = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="private question",
        created_at=utc_now(),
    )
    injected = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="injected",
        created_at=utc_now(),
    )
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.users.add(other)
        await uow.documents.add(document)
        await uow.document_content.add_pages([page])
        await uow.conversations.add(conversation)
        await uow.messages.add(owner.id, message)
        await uow.commit()

    async with database.unit_of_work() as uow:
        assert await uow.document_content.list_pages(other.id, document.id) == []
        assert await uow.document_content.list_chunks(other.id, document.id) == []
        assert await uow.messages.list_for_conversation(other.id, conversation.id) == []
        assert await uow.messages.list_citations_for_conversation(other.id, conversation.id) == {}
        with pytest.raises(NotFoundError):
            await uow.messages.add(other.id, injected)


async def test_the_database_bumps_updated_at_when_a_caller_forgets(
    database: Database, factories: type[Factories]
) -> None:
    owner = factories.user()
    collection = factories.collection(owner.id)
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.collections.add(collection)
        await uow.commit()

    renamed = Collection(
        id=collection.id,
        owner_id=owner.id,
        name="Renamed",
        description=None,
        created_at=collection.created_at,
        updated_at=collection.updated_at,  # stale on purpose
    )
    async with database.unit_of_work() as uow:
        await uow.collections.update(renamed)
        await uow.commit()

    async with database.unit_of_work() as uow:
        stored = await uow.collections.get(owner.id, collection.id)
        assert stored is not None
        assert stored.name == "Renamed"
        assert stored.updated_at > collection.updated_at


async def test_an_unreachable_database_surfaces_as_a_retryable_domain_error() -> None:
    unreachable = Database(
        CoreSettings(
            _env_file=None,
            database_url=SecretStr("postgresql+asyncpg://u:p@127.0.0.1:1/doculens"),
        )
    )
    try:
        with pytest.raises(DatabaseUnavailableError):
            async with unreachable.unit_of_work() as uow:
                await uow.users.get(new_id())
    finally:
        await unreachable.dispose()
