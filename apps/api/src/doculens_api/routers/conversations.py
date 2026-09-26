"""Conversations and their message history (SPECIFICATIONS.md §21, §23, §28, §33).

Posting a question to a conversation runs the answering pipeline and persists both turns; the
citations are returned with each assistant message so the client can render them separately
from the answer text (§23). Every route addresses the caller's own conversations only (§9).
"""

from datetime import datetime
from http import HTTPStatus
from typing import Self
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from doculens.application.conversations import MessageWithCitations
from doculens.application.rag import RagQuery
from doculens.application.ratelimit import enforce
from doculens.domain.answering import AnswerOutcome, AnswerResult
from doculens.domain.common import UNSET
from doculens.domain.conversations import (
    MAX_CONVERSATION_TITLE_LENGTH,
    Citation,
    Conversation,
    Message,
    MessageRole,
)
from doculens_api.dependencies import (
    ConversationServiceDep,
    CurrentUserDep,
    RagServiceDep,
    RateLimiterDep,
    SettingsDep,
)
from doculens_api.errors import (
    BAD_REQUEST_RESPONSE,
    BEARER_AUTH_RESPONSES,
    NOT_FOUND_RESPONSE,
    RATE_LIMITED_RESPONSE,
    ErrorResponse,
)

router = APIRouter(
    prefix="/api/v1/conversations",
    tags=["conversations"],
    responses=BEARER_AUTH_RESPONSES,
)


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
        return cls.of(item.message, item.citations)

    @classmethod
    def of(cls, message: Message, citations: list[Citation] | tuple[Citation, ...]) -> Self:
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            citations=[CitationResponse.from_citation(c) for c in citations],
        )


class AskRequest(BaseModel):
    """A question for the conversation; ``document_ids`` narrows retrieval to those documents
    for this question only (§27), otherwise the conversation's collection or every document."""

    question: str = Field(
        min_length=1,
        max_length=100_000,
        description=(
            "Natural-language question. Transport max matches `RETRIEVAL_MAX_QUERY_CHARACTERS` "
            "upper bound (100000); the effective cap is the deployed setting (default 2000)."
        ),
    )
    document_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)


class RetrievalMetadataResponse(BaseModel):
    query: str
    rewritten: bool
    retriever: str
    documents_in_scope: int
    hits: int
    evidence: int
    context_items: int
    reranking: str
    model: str | None
    prompt_version: str | None
    invalid_references: int
    truncated: bool
    uncited: bool


class UsageResponse(BaseModel):
    embedding_requests: int
    embedding_tokens: int | None
    llm_requests: int
    input_tokens: int | None
    output_tokens: int | None


class TimingResponse(BaseModel):
    rewrite_ms: int
    retrieval_ms: int
    generation_ms: int
    persistence_ms: int
    total_ms: int


class AnswerResponse(BaseModel):
    """Both persisted turns of the exchange plus what producing the answer involved (§21, §38)."""

    conversation_id: UUID
    outcome: AnswerOutcome
    user_message: MessageResponse
    assistant_message: MessageResponse
    retrieval: RetrievalMetadataResponse
    usage: UsageResponse
    timing: TimingResponse

    @classmethod
    def from_result(cls, result: AnswerResult) -> Self:
        retrieval = result.retrieval
        usage = result.usage
        return cls(
            conversation_id=result.conversation_id,
            outcome=result.outcome,
            user_message=MessageResponse.of(result.user_message, ()),
            assistant_message=MessageResponse.of(result.assistant_message, result.citations),
            retrieval=RetrievalMetadataResponse(
                query=retrieval.query,
                rewritten=retrieval.rewritten,
                retriever=retrieval.retriever,
                documents_in_scope=retrieval.documents_in_scope,
                hits=retrieval.hits,
                evidence=retrieval.evidence,
                context_items=retrieval.context_items,
                reranking=retrieval.reranking.status.value,
                model=retrieval.model,
                prompt_version=retrieval.prompt_version,
                invalid_references=retrieval.invalid_references,
                truncated=result.truncated,
                uncited=result.uncited,
            ),
            usage=UsageResponse(
                embedding_requests=usage.embeddings.requests,
                embedding_tokens=usage.embeddings.tokens,
                llm_requests=usage.llm.requests,
                input_tokens=usage.llm.input_tokens,
                output_tokens=usage.llm.output_tokens,
            ),
            timing=TimingResponse(
                rewrite_ms=result.timing.rewrite_ms,
                retrieval_ms=result.timing.retrieval_ms,
                generation_ms=result.timing.generation_ms,
                persistence_ms=result.timing.persistence_ms,
                total_ms=result.timing.total_ms,
            ),
        )


class CreateConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_CONVERSATION_TITLE_LENGTH)
    collection_id: UUID | None = None


class UpdateConversationRequest(BaseModel):
    """Fields left out are unchanged; ``collection_id: null`` detaches the conversation."""

    title: str | None = Field(default=None, min_length=1, max_length=MAX_CONVERSATION_TITLE_LENGTH)
    collection_id: UUID | None = None


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Create a conversation",
    responses={**NOT_FOUND_RESPONSE, **BAD_REQUEST_RESPONSE},
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


@router.get("/{conversation_id}", summary="Inspect a conversation", responses=NOT_FOUND_RESPONSE)
async def get_conversation(
    conversation_id: UUID, user: CurrentUserDep, conversations: ConversationServiceDep
) -> ConversationResponse:
    conversation = await conversations.get(user.id, conversation_id)
    return ConversationResponse.from_conversation(conversation)


@router.patch(
    "/{conversation_id}",
    summary="Rename or move a conversation",
    responses={**NOT_FOUND_RESPONSE, **BAD_REQUEST_RESPONSE},
)
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
    responses=NOT_FOUND_RESPONSE,
)
async def delete_conversation(
    conversation_id: UUID, user: CurrentUserDep, conversations: ConversationServiceDep
) -> Response:
    await conversations.delete(user.id, conversation_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.post(
    "/{conversation_id}/messages",
    status_code=HTTPStatus.CREATED,
    summary="Ask a question in the conversation and persist the grounded, cited answer",
    responses={
        **NOT_FOUND_RESPONSE,
        **BAD_REQUEST_RESPONSE,
        **RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "A provider did not answer in time; nothing was recorded, retry.",
        },
    },
)
async def ask_question(
    conversation_id: UUID,
    body: AskRequest,
    user: CurrentUserDep,
    rag: RagServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> AnswerResponse:
    await enforce(
        limiter,
        f"ask:user:{user.id}",
        limit=settings.ask_rate_limit_attempts,
        window_seconds=settings.ask_rate_limit_window_seconds,
    )
    result = await rag.answer(
        RagQuery(owner_id=user.id, question=body.question, document_ids=body.document_ids),
        conversation_id=conversation_id,
    )
    return AnswerResponse.from_result(result)


@router.get(
    "/{conversation_id}/messages",
    summary="The conversation's messages with their citations, oldest first",
    responses=NOT_FOUND_RESPONSE,
)
async def list_messages(
    conversation_id: UUID, user: CurrentUserDep, conversations: ConversationServiceDep
) -> list[MessageResponse]:
    history = await conversations.list_messages(user.id, conversation_id)
    return [MessageResponse.from_message(item) for item in history]
