"""Conversations and their message history (SPECIFICATIONS.md §28, §33).

Posting a question (creating messages) is the RAG phase; citations are returned with each message
so the client can render them separately from the answer text (§23).
"""

from datetime import datetime
from http import HTTPStatus
from typing import Any, Self
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from doculens.application.conversations import MessageWithCitations
from doculens.domain.common import UNSET
from doculens.domain.conversations import (
    MAX_CONVERSATION_TITLE_LENGTH,
    Citation,
    Conversation,
    MessageRole,
)
from doculens_api.dependencies import ConversationServiceDep, CurrentUserDep

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    HTTPStatus.NOT_FOUND: {"description": "No such conversation or collection for this user."}
}


class ConversationResponse(BaseModel):
    id: UUID
    collection_id: UUID | None
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_conversation(cls, conversation: Conversation) -> Self:
        return cls(
            id=conversation.id,
            collection_id=conversation.collection_id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )


class CitationResponse(BaseModel):
    id: UUID
    document_id: UUID
    page_number: int
    chunk_id: UUID | None
    quoted_text: str
    retrieval_score: float
    reranking_score: float | None
    citation_order: int

    @classmethod
    def from_citation(cls, citation: Citation) -> Self:
        return cls(
            id=citation.id,
            document_id=citation.document_id,
            page_number=citation.page_number,
            chunk_id=citation.chunk_id,
            quoted_text=citation.quoted_text,
            retrieval_score=citation.retrieval_score,
            reranking_score=citation.reranking_score,
            citation_order=citation.citation_order,
        )


class MessageResponse(BaseModel):
    id: UUID
    role: MessageRole
    content: str
    created_at: datetime
    citations: list[CitationResponse]

    @classmethod
    def from_message(cls, item: MessageWithCitations) -> Self:
        return cls(
            id=item.message.id,
            role=item.message.role,
            content=item.message.content,
            created_at=item.message.created_at,
            citations=[CitationResponse.from_citation(c) for c in item.citations],
        )


class CreateConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_CONVERSATION_TITLE_LENGTH)
    collection_id: UUID | None = None


class UpdateConversationRequest(BaseModel):
    """Fields left out are unchanged; ``collection_id: null`` detaches the conversation."""

    title: str | None = Field(default=None, min_length=1, max_length=MAX_CONVERSATION_TITLE_LENGTH)
    collection_id: UUID | None = None


@router.post(
    "", status_code=HTTPStatus.CREATED, summary="Create a conversation", responses=NOT_FOUND
)
async def create_conversation(
    body: CreateConversationRequest, user: CurrentUserDep, conversations: ConversationServiceDep
) -> ConversationResponse:
    conversation = await conversations.create(
        user.id, title=body.title, collection_id=body.collection_id
    )
    return ConversationResponse.from_conversation(conversation)


@router.get("", summary="List the user's conversations, most recently updated first")
async def list_conversations(
    user: CurrentUserDep, conversations: ConversationServiceDep
) -> list[ConversationResponse]:
    listed = await conversations.list_for_owner(user.id)
    return [ConversationResponse.from_conversation(c) for c in listed]


@router.get("/{conversation_id}", summary="Inspect a conversation", responses=NOT_FOUND)
async def get_conversation(
    conversation_id: UUID, user: CurrentUserDep, conversations: ConversationServiceDep
) -> ConversationResponse:
    conversation = await conversations.get(user.id, conversation_id)
    return ConversationResponse.from_conversation(conversation)


@router.patch("/{conversation_id}", summary="Rename or move a conversation", responses=NOT_FOUND)
async def update_conversation(
    conversation_id: UUID,
    body: UpdateConversationRequest,
    user: CurrentUserDep,
    conversations: ConversationServiceDep,
) -> ConversationResponse:
    conversation = await conversations.update(
        user.id,
        conversation_id,
        title=body.title if body.title is not None else UNSET,
        collection_id=body.collection_id if "collection_id" in body.model_fields_set else UNSET,
    )
    return ConversationResponse.from_conversation(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Delete a conversation and its messages",
    responses=NOT_FOUND,
)
async def delete_conversation(
    conversation_id: UUID, user: CurrentUserDep, conversations: ConversationServiceDep
) -> Response:
    await conversations.delete(user.id, conversation_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.get(
    "/{conversation_id}/messages",
    summary="The conversation's messages with their citations, oldest first",
    responses=NOT_FOUND,
)
async def list_messages(
    conversation_id: UUID, user: CurrentUserDep, conversations: ConversationServiceDep
) -> list[MessageResponse]:
    history = await conversations.list_messages(user.id, conversation_id)
    return [MessageResponse.from_message(item) for item in history]
