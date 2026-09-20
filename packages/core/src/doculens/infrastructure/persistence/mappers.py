"""Translate between domain entities and ORM rows. Nothing else touches both."""

from doculens.domain.collections import Collection
from doculens.domain.conversations import Citation, Conversation, Message
from doculens.domain.documents import Document, DocumentChunk, DocumentPage
from doculens.domain.users import User
from doculens.infrastructure.persistence.models import (
    CitationModel,
    CollectionModel,
    ConversationModel,
    DocumentChunkModel,
    DocumentModel,
    DocumentPageModel,
    MessageModel,
    UserModel,
)


def user_to_domain(row: UserModel) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_login_at=row.last_login_at,
    )


def user_to_row(user: User) -> UserModel:
    row = UserModel(id=user.id)
    apply_user(row, user)
    return row


def apply_user(row: UserModel, user: User) -> None:
    row.email = user.email
    row.password_hash = user.password_hash
    row.status = user.status
    row.created_at = user.created_at
    row.updated_at = user.updated_at
    row.last_login_at = user.last_login_at


def collection_to_domain(row: CollectionModel) -> Collection:
    return Collection(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def collection_to_row(collection: Collection) -> CollectionModel:
    row = CollectionModel(id=collection.id, owner_id=collection.owner_id)
    apply_collection(row, collection)
    return row


def apply_collection(row: CollectionModel, collection: Collection) -> None:
    row.name = collection.name
    row.description = collection.description
    row.created_at = collection.created_at
    row.updated_at = collection.updated_at


def document_to_domain(row: DocumentModel) -> Document:
    return Document(
        id=row.id,
        owner_id=row.owner_id,
        collection_id=row.collection_id,
        filename=row.filename,
        storage_key=row.storage_key,
        content_hash=row.content_hash,
        mime_type=row.mime_type,
        file_size=row.file_size,
        page_count=row.page_count,
        processing_status=row.processing_status,
        processing_error=row.processing_error,
        chunk_count=row.chunk_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        indexed_at=row.indexed_at,
    )


def document_to_row(document: Document) -> DocumentModel:
    row = DocumentModel(id=document.id, owner_id=document.owner_id)
    apply_document(row, document)
    return row


def apply_document(row: DocumentModel, document: Document) -> None:
    row.collection_id = document.collection_id
    row.filename = document.filename
    row.storage_key = document.storage_key
    row.content_hash = document.content_hash
    row.mime_type = document.mime_type
    row.file_size = document.file_size
    row.page_count = document.page_count
    row.processing_status = document.processing_status
    row.processing_error = document.processing_error
    row.chunk_count = document.chunk_count
    row.created_at = document.created_at
    row.updated_at = document.updated_at
    row.indexed_at = document.indexed_at


def page_to_domain(row: DocumentPageModel) -> DocumentPage:
    return DocumentPage(
        id=row.id,
        document_id=row.document_id,
        page_number=row.page_number,
        extracted_text=row.extracted_text,
        character_count=row.character_count,
        metadata=dict(row.page_metadata),
    )


def page_to_row(page: DocumentPage) -> DocumentPageModel:
    return DocumentPageModel(
        id=page.id,
        document_id=page.document_id,
        page_number=page.page_number,
        extracted_text=page.extracted_text,
        character_count=page.character_count,
        page_metadata=dict(page.metadata),
    )


def chunk_to_domain(row: DocumentChunkModel) -> DocumentChunk:
    return DocumentChunk(
        id=row.id,
        document_id=row.document_id,
        page_id=row.page_id,
        chunk_index=row.chunk_index,
        text=row.text,
        token_count=row.token_count,
        metadata=dict(row.chunk_metadata),
        vector_id=row.vector_id,
    )


def chunk_to_row(chunk: DocumentChunk) -> DocumentChunkModel:
    return DocumentChunkModel(
        id=chunk.id,
        document_id=chunk.document_id,
        page_id=chunk.page_id,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        token_count=chunk.token_count,
        chunk_metadata=dict(chunk.metadata),
        vector_id=chunk.vector_id,
    )


def conversation_to_domain(row: ConversationModel) -> Conversation:
    return Conversation(
        id=row.id,
        owner_id=row.owner_id,
        collection_id=row.collection_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def conversation_to_row(conversation: Conversation) -> ConversationModel:
    row = ConversationModel(id=conversation.id, owner_id=conversation.owner_id)
    apply_conversation(row, conversation)
    return row


def apply_conversation(row: ConversationModel, conversation: Conversation) -> None:
    row.collection_id = conversation.collection_id
    row.title = conversation.title
    row.created_at = conversation.created_at
    row.updated_at = conversation.updated_at


def message_to_domain(row: MessageModel) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=row.role,
        content=row.content,
        created_at=row.created_at,
    )


def message_to_row(message: Message) -> MessageModel:
    return MessageModel(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )


def citation_to_domain(row: CitationModel) -> Citation:
    return Citation(
        id=row.id,
        message_id=row.message_id,
        document_id=row.document_id,
        page_number=row.page_number,
        chunk_id=row.chunk_id,
        quoted_text=row.quoted_text,
        retrieval_score=row.retrieval_score,
        reranking_score=row.reranking_score,
        citation_order=row.citation_order,
    )


def citation_to_row(citation: Citation) -> CitationModel:
    return CitationModel(
        id=citation.id,
        message_id=citation.message_id,
        document_id=citation.document_id,
        page_number=citation.page_number,
        chunk_id=citation.chunk_id,
        quoted_text=citation.quoted_text,
        retrieval_score=citation.retrieval_score,
        reranking_score=citation.reranking_score,
        citation_order=citation.citation_order,
    )
