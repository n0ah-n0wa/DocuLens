"""The database enforces the integrity rules of SPECIFICATIONS.md §7, §30, §31 and §45."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from doculens.domain.conversations import Citation, Message, MessageRole
from doculens.domain.documents import DocumentChunk, DocumentPage
from doculens.domain.errors import ConflictError
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.persistence.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentModel,
    DocumentPageModel,
)
from doculens.testing.factories import Factories

pytestmark = pytest.mark.integration


def _page(document_id: UUID, page_number: int = 1) -> DocumentPage:
    return DocumentPage(
        id=new_id(),
        document_id=document_id,
        page_number=page_number,
        extracted_text="",
        character_count=0,
        metadata={},
    )


def _chunk(document_id: UUID, page_id: UUID, chunk_index: int = 0) -> DocumentChunk:
    return DocumentChunk(
        id=new_id(),
        document_id=document_id,
        page_id=page_id,
        chunk_index=chunk_index,
        text="x",
        token_count=1,
        metadata={},
    )


async def _seed_document(database: Database, factories: type[Factories]) -> tuple[UUID, UUID, UUID]:
    owner = factories.user()
    collection = factories.collection(owner.id)
    document = factories.document(owner.id, collection.id)
    async with database.unit_of_work() as uow:
        await uow.users.add(owner)
        await uow.collections.add(collection)
        await uow.documents.add(document)
        await uow.commit()
    return owner.id, collection.id, document.id


async def test_email_is_unique(database: Database, factories: type[Factories]) -> None:
    async with database.unit_of_work() as uow:
        await uow.users.add(factories.user("same@example.com"))
        with pytest.raises(IntegrityError, match="uq_users_email"):
            await uow.users.add(factories.user("same@example.com"))


async def test_page_numbers_and_chunk_indexes_are_unique_per_document(
    database: Database, factories: type[Factories]
) -> None:
    _, _, document_id = await _seed_document(database, factories)
    page = _page(document_id)

    async with database.unit_of_work() as uow:
        await uow.document_content.add_pages([page])
        # Duplicate page numbers are a domain conflict (two extractions of one document).
        with pytest.raises(ConflictError):
            await uow.document_content.add_pages([_page(document_id)])

    async with database.unit_of_work() as uow:
        await uow.document_content.add_pages([page])
        with pytest.raises(IntegrityError, match="uq_document_chunks_document_id_chunk_index"):
            await uow.document_content.add_chunks(
                [_chunk(document_id, page.id), _chunk(document_id, page.id)]
            )


async def test_check_constraints_reject_invalid_numbers(
    database: Database, factories: type[Factories]
) -> None:
    _, _, document_id = await _seed_document(database, factories)

    async with database.unit_of_work() as uow:
        with pytest.raises(IntegrityError, match="ck_document_pages_page_number_positive"):
            await uow.document_content.add_pages([_page(document_id, page_number=0)])


async def test_deleting_a_collection_detaches_its_documents_and_conversations(
    database: Database, factories: type[Factories]
) -> None:
    owner_id, collection_id, document_id = await _seed_document(database, factories)
    conversation = factories.conversation(owner_id, collection_id)
    async with database.unit_of_work() as uow:
        await uow.conversations.add(conversation)
        await uow.commit()

    async with database.unit_of_work() as uow:
        assert await uow.collections.delete(owner_id, collection_id) is True
        await uow.commit()

    async with database.unit_of_work() as uow:
        document = await uow.documents.get(owner_id, document_id)
        stored_conversation = await uow.conversations.get(owner_id, conversation.id)
        assert document is not None
        assert document.collection_id is None
        assert stored_conversation is not None
        assert stored_conversation.collection_id is None


async def test_deleting_a_document_cascades_to_pages_and_chunks(
    database: Database, factories: type[Factories]
) -> None:
    _, _, document_id = await _seed_document(database, factories)
    page = _page(document_id)
    async with database.unit_of_work() as uow:
        await uow.document_content.add_pages([page])
        await uow.document_content.add_chunks([_chunk(document_id, page.id)])
        await uow.commit()

    async with database.session_factory() as session:
        row = await session.get(DocumentModel, document_id)
        assert row is not None
        await session.delete(row)
        await session.commit()

    async with database.session_factory() as session:
        assert (await session.scalars(select(DocumentPageModel))).all() == []
        assert (await session.scalars(select(DocumentChunkModel))).all() == []


async def test_a_cited_document_cannot_be_hard_deleted_and_citations_survive_reindexing(
    database: Database, factories: type[Factories]
) -> None:
    owner_id, _, document_id = await _seed_document(database, factories)
    page = _page(document_id, page_number=3)
    chunk = _chunk(document_id, page.id)
    conversation = factories.conversation(owner_id)
    answer = Message(
        id=new_id(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Based on the evidence…",
        created_at=utc_now(),
    )
    citation = Citation(
        id=new_id(),
        message_id=answer.id,
        document_id=document_id,
        page_number=3,
        quoted_text="evidence",
        retrieval_score=0.9,
        citation_order=0,
        chunk_id=chunk.id,
    )
    async with database.unit_of_work() as uow:
        await uow.document_content.add_pages([page])
        await uow.document_content.add_chunks([chunk])
        await uow.conversations.add(conversation)
        await uow.messages.add(owner_id, answer, [citation])
        await uow.commit()

    # Re-indexing replaces chunks: the citation keeps its quoted text and loses only the chunk link.
    async with database.unit_of_work() as uow:
        await uow.document_content.delete_content(document_id)
        await uow.commit()
    async with database.unit_of_work() as uow:
        grouped = await uow.messages.list_citations_for_conversation(owner_id, conversation.id)
        (stored,) = grouped[answer.id]
        assert stored.chunk_id is None
        assert stored.quoted_text == "evidence"

    # Hard-deleting a cited document is refused until OQ-7 decides tombstoning.
    async with database.session_factory() as session:
        row = await session.get(DocumentModel, document_id)
        assert row is not None
        await session.delete(row)
        with pytest.raises(IntegrityError, match="fk_citations_document_id_documents"):
            await session.commit()

    async with database.session_factory() as session:
        assert (await session.scalars(select(CitationModel))).one().document_id == document_id


async def test_emails_must_be_stored_lower_case(
    database: Database, factories: type[Factories]
) -> None:
    async with database.unit_of_work() as uow:
        with pytest.raises(IntegrityError, match="ck_users_email_lowercase"):
            await uow.users.add(factories.user("Mixed.Case@Example.com"))
