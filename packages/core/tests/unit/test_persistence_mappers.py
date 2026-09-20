"""Domain ↔ ORM mapping is lossless and never hands ORM objects to the domain."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from doculens.domain.collections import Collection
from doculens.domain.conversations import Citation, Conversation, Message, MessageRole
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.users import User, UserStatus
from doculens.infrastructure.persistence import mappers

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def test_user_round_trip() -> None:
    user = User(
        id=uuid4(),
        email="a@example.com",
        password_hash="$argon2id$hash",  # noqa: S106 - a hash placeholder, not a credential
        status=UserStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        last_login_at=None,
    )

    assert mappers.user_to_domain(mappers.user_to_row(user)) == user


def test_collection_round_trip() -> None:
    collection = Collection(
        id=uuid4(),
        owner_id=uuid4(),
        name="Contracts",
        description=None,
        created_at=NOW,
        updated_at=NOW,
    )

    assert mappers.collection_to_domain(mappers.collection_to_row(collection)) == collection


def test_document_round_trip_keeps_every_field() -> None:
    document = Document(
        id=uuid4(),
        owner_id=uuid4(),
        collection_id=uuid4(),
        filename="a.pdf",
        storage_key="documents/x/y/original.pdf",
        content_hash="f" * 64,
        mime_type="application/pdf",
        file_size=10,
        processing_status=ProcessingStatus.READY,
        created_at=NOW,
        updated_at=NOW,
        page_count=3,
        processing_error=None,
        chunk_count=7,
        indexed_at=NOW,
    )

    assert mappers.document_to_domain(mappers.document_to_row(document)) == document


def test_page_and_chunk_metadata_are_copied_not_shared() -> None:
    metadata = {"has_text": True}
    page = DocumentPage(
        id=uuid4(),
        document_id=uuid4(),
        page_number=1,
        extracted_text="hello",
        character_count=5,
        metadata=metadata,
    )
    chunk = DocumentChunk(
        id=uuid4(),
        document_id=page.document_id,
        page_id=page.id,
        chunk_index=0,
        text="hello",
        token_count=1,
        metadata=metadata,
        vector_id="chunk-vector",
    )

    page_row = mappers.page_to_row(page)
    chunk_row = mappers.chunk_to_row(chunk)
    metadata["has_text"] = False

    assert page_row.page_metadata == {"has_text": True}
    assert chunk_row.chunk_metadata == {"has_text": True}
    assert mappers.page_to_domain(page_row).metadata == {"has_text": True}
    assert mappers.chunk_to_domain(chunk_row) == DocumentChunk(
        id=chunk.id,
        document_id=chunk.document_id,
        page_id=chunk.page_id,
        chunk_index=0,
        text="hello",
        token_count=1,
        metadata={"has_text": True},
        vector_id="chunk-vector",
    )


def test_conversation_message_and_citation_round_trip() -> None:
    conversation = Conversation(
        id=uuid4(),
        owner_id=uuid4(),
        collection_id=None,
        title="Risks",
        created_at=NOW,
        updated_at=NOW,
    )
    message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Three risks are identified.",
        created_at=NOW,
    )
    citation = Citation(
        id=uuid4(),
        message_id=message.id,
        document_id=uuid4(),
        page_number=17,
        quoted_text="…the three main risks…",
        retrieval_score=0.87,
        citation_order=0,
        chunk_id=uuid4(),
        reranking_score=0.91,
    )

    assert mappers.conversation_to_domain(mappers.conversation_to_row(conversation)) == conversation
    assert mappers.message_to_domain(mappers.message_to_row(message)) == message
    assert mappers.citation_to_domain(mappers.citation_to_row(citation)) == citation
