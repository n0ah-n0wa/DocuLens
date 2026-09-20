"""Collection, document and conversation use cases enforce ownership structurally (§9)."""

from uuid import uuid4

import pytest

from doculens.application.collections import CollectionService
from doculens.application.conversations import ConversationService
from doculens.application.documents import DocumentService
from doculens.domain.collections import CollectionNotFoundError
from doculens.domain.conversations import ConversationNotFoundError, Message, MessageRole
from doculens.domain.documents import DocumentNotFoundError
from doculens.domain.errors import InvalidInputError
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork

pytestmark = pytest.mark.unit


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def collections(store: InMemoryStore) -> CollectionService:
    return CollectionService(unit_of_work=lambda: InMemoryUnitOfWork(store))


@pytest.fixture
def documents(store: InMemoryStore) -> DocumentService:
    return DocumentService(unit_of_work=lambda: InMemoryUnitOfWork(store))


@pytest.fixture
def conversations(store: InMemoryStore) -> ConversationService:
    return ConversationService(unit_of_work=lambda: InMemoryUnitOfWork(store))


ALICE = uuid4()
BOB = uuid4()


async def test_collections_are_private_to_their_owner(collections: CollectionService) -> None:
    mine = await collections.create(ALICE, name="  Contracts ", description="  ")

    assert mine.name == "Contracts"
    assert mine.description is None
    assert await collections.list_for_owner(ALICE) == [mine]
    assert await collections.list_for_owner(BOB) == []
    assert await collections.get(ALICE, mine.id) == mine

    with pytest.raises(CollectionNotFoundError):
        await collections.get(BOB, mine.id)
    with pytest.raises(CollectionNotFoundError):
        await collections.update(BOB, mine.id, name="Stolen")
    with pytest.raises(CollectionNotFoundError):
        await collections.delete(BOB, mine.id)
    assert (await collections.get(ALICE, mine.id)).name == "Contracts"

    renamed = await collections.update(ALICE, mine.id, name="Legal", description="All contracts")
    assert (renamed.name, renamed.description) == ("Legal", "All contracts")
    cleared = await collections.update(ALICE, mine.id, description=None)
    assert (cleared.name, cleared.description) == ("Legal", None)

    await collections.delete(ALICE, mine.id)
    with pytest.raises(CollectionNotFoundError):
        await collections.get(ALICE, mine.id)


async def test_collection_names_are_validated(collections: CollectionService) -> None:
    with pytest.raises(InvalidInputError, match="name must not be empty"):
        await collections.create(ALICE, name="   ", description=None)
    with pytest.raises(InvalidInputError, match="at most 200"):
        await collections.create(ALICE, name="x" * 201, description=None)


async def test_documents_are_private_and_cannot_be_moved_into_foreign_collections(
    store: InMemoryStore, documents: DocumentService, collections: CollectionService
) -> None:
    alice_collection = await collections.create(ALICE, name="Alice's", description=None)
    bob_collection = await collections.create(BOB, name="Bob's", description=None)
    document = Factories.document(ALICE, alice_collection.id)
    store.documents[document.id] = document

    assert await documents.list_for_owner(ALICE) == [document]
    assert await documents.list_for_owner(ALICE, collection_id=alice_collection.id) == [document]
    assert await documents.list_for_owner(BOB) == []
    assert await documents.get(ALICE, document.id) == document

    with pytest.raises(DocumentNotFoundError):
        await documents.get(BOB, document.id)
    with pytest.raises(DocumentNotFoundError):
        await documents.update(BOB, document.id, filename="stolen.pdf")
    with pytest.raises(CollectionNotFoundError):
        await documents.list_for_owner(BOB, collection_id=alice_collection.id)
    with pytest.raises(CollectionNotFoundError):
        await documents.update(ALICE, document.id, collection_id=bob_collection.id)
    assert store.documents[document.id] == document

    moved = await documents.update(ALICE, document.id, filename=" renamed.pdf ", collection_id=None)
    assert (moved.filename, moved.collection_id) == ("renamed.pdf", None)
    assert store.documents[document.id] == moved


async def test_filenames_never_contain_path_separators(
    store: InMemoryStore, documents: DocumentService
) -> None:
    document = Factories.document(ALICE)
    store.documents[document.id] = document

    for bad in ("../etc/passwd", "a/b.pdf", "a\\b.pdf", ".", ""):
        with pytest.raises(InvalidInputError):
            await documents.update(ALICE, document.id, filename=bad)


async def test_conversations_and_messages_are_private(
    store: InMemoryStore, conversations: ConversationService, collections: CollectionService
) -> None:
    bob_collection = await collections.create(BOB, name="Bob's", description=None)
    mine = await conversations.create(ALICE, title=" Risks ", collection_id=None)
    question = Message(
        id=new_id(),
        conversation_id=mine.id,
        role=MessageRole.USER,
        content="What are the risks?",
        created_at=utc_now(),
    )
    store.messages[question.id] = question

    assert mine.title == "Risks"
    assert await conversations.list_for_owner(ALICE) == [mine]
    assert await conversations.list_for_owner(BOB) == []
    history = await conversations.list_messages(ALICE, mine.id)
    assert [item.message for item in history] == [question]

    with pytest.raises(ConversationNotFoundError):
        await conversations.get(BOB, mine.id)
    with pytest.raises(ConversationNotFoundError):
        await conversations.list_messages(BOB, mine.id)
    with pytest.raises(ConversationNotFoundError):
        await conversations.update(BOB, mine.id, title="Stolen")
    with pytest.raises(ConversationNotFoundError):
        await conversations.delete(BOB, mine.id)
    with pytest.raises(CollectionNotFoundError):
        await conversations.create(ALICE, title="In Bob's", collection_id=bob_collection.id)
    with pytest.raises(CollectionNotFoundError):
        await conversations.update(ALICE, mine.id, collection_id=bob_collection.id)
    assert store.conversations[mine.id] == mine
    assert question.id in store.messages

    await conversations.delete(ALICE, mine.id)
    assert question.id not in store.messages
