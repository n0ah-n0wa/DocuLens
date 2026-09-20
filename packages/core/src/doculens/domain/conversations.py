"""Conversation, message and citation entities (SPECIFICATIONS.md §7.6 to §7.8, §28).

Messages are immutable after creation. Citations are persisted independently of the answer text
and keep the quoted evidence, so they remain meaningful even when a chunk is later replaced by
re-indexing (§23, §32).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from doculens.domain.errors import NotFoundError

MAX_CONVERSATION_TITLE_LENGTH = 200


class ConversationNotFoundError(NotFoundError):
    """Also raised for conversations owned by someone else: existence is never disclosed (§9)."""

    code = "CONVERSATION_NOT_FOUND"
    default_message = "The conversation was not found."


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True, slots=True)
class Conversation:
    id: UUID
    owner_id: UUID
    collection_id: UUID | None
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Citation:
    id: UUID
    message_id: UUID
    document_id: UUID
    page_number: int
    quoted_text: str
    retrieval_score: float
    citation_order: int
    chunk_id: UUID | None = None
    reranking_score: float | None = None
