"""Persistence ports: repositories and the unit of work (SPECIFICATIONS.md §45, §46, §72).

Repositories speak in domain entities only; ORM models never cross this boundary. Every method
that reads a user-owned resource takes the owner's ID and returns nothing for other owners, so
ownership enforcement (§9) is structural rather than a convention.

A unit of work is one transaction: repositories obtained from it share the same connection, and
nothing is durable until ``commit`` is awaited. Leaving the context without committing rolls back.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from doculens.domain.auth import RefreshToken
from doculens.domain.collections import Collection
from doculens.domain.conversations import Citation, Conversation, Message
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.retrieval import ChunkMatch
from doculens.domain.users import User
from doculens.domain.vectors import SearchFilter


class UserRepository(Protocol):
    async def add(self, user: User) -> None: ...
    async def get(self, user_id: UUID) -> User | None: ...
    async def lock(self, user_id: UUID) -> None:
        """Serialise concurrent per-user checks (quotas) until the unit of work ends."""
        ...

    async def get_by_email(self, email: str) -> User | None: ...
    async def update(self, user: User) -> None: ...


class RefreshTokenRepository(Protocol):
    """Durable refresh-token records so revocation survives restarts (§8, §47)."""

    async def add(self, token: RefreshToken) -> None: ...
    async def get(self, token_id: UUID) -> RefreshToken | None: ...
    async def rotate(self, token_id: UUID, successor_id: UUID, *, now: datetime) -> bool:
        """Atomically mark the token as replaced, only if it is still unconsumed.

        Returns ``False`` when the token was already rotated or revoked (by a concurrent request
        or an attacker), so the caller can treat the presentation as reuse.
        """
        ...

    async def revoke_family(self, family_id: UUID, *, now: datetime) -> int:
        """Revoke every still-active token of the family; returns how many were revoked."""
        ...


class CollectionRepository(Protocol):
    async def add(self, collection: Collection) -> None: ...
    async def get(self, owner_id: UUID, collection_id: UUID) -> Collection | None: ...
    async def list_for_owner(self, owner_id: UUID) -> list[Collection]: ...
    async def update(self, collection: Collection) -> None: ...
    async def delete(self, owner_id: UUID, collection_id: UUID) -> bool: ...


class DocumentRepository(Protocol):
    async def add(self, document: Document) -> None:
        """Insert; raises ``DuplicateDocumentError`` when the owner already has this content."""
        ...

    async def get_for_processing(self, document_id: UUID) -> Document | None:
        """Unscoped read for the worker, which acts on job identifiers rather than for a user."""
        ...

    async def find_by_content_hash(self, owner_id: UUID, content_hash: str) -> Document | None:
        """The owner's live (not deleted) document with this content, if any (§49)."""
        ...

    async def compare_and_update(
        self, document: Document, *, expected_status: ProcessingStatus
    ) -> bool:
        """Write ``document`` only if the row is still in ``expected_status``; False otherwise."""
        ...

    async def get(self, owner_id: UUID, document_id: UUID) -> Document | None: ...
    async def list_for_owner(self, owner_id: UUID) -> list[Document]: ...
    async def list_in_collection(self, owner_id: UUID, collection_id: UUID) -> list[Document]: ...
    async def count_for_owner(self, owner_id: UUID) -> int: ...
    async def update(self, document: Document) -> None: ...


class DocumentContentRepository(Protocol):
    """Pages and chunks: owned by a document.

    Writes come from the processing pipeline, which has already loaded the document; reads take
    the owner so that page text is never served across users.
    """

    async def add_pages(self, pages: Sequence[DocumentPage]) -> None: ...
    async def add_chunks(self, chunks: Sequence[DocumentChunk]) -> None: ...
    async def replace_chunks(self, document_id: UUID, chunks: Sequence[DocumentChunk]) -> None:
        """Make ``chunks`` the document's complete chunk set.

        Existing rows with the same id are updated in place (their citations survive), rows
        whose id is not in the new set are removed (§32: re-indexing never duplicates).
        """
        ...

    async def set_vector_ids(self, document_id: UUID, vector_ids: Mapping[UUID, str]) -> None:
        """Record which vector holds each chunk once indexing succeeded (§7.5 ``vector_id``)."""
        ...

    async def list_pages(self, owner_id: UUID, document_id: UUID) -> list[DocumentPage]: ...
    async def list_chunks(self, owner_id: UUID, document_id: UUID) -> list[DocumentChunk]: ...
    async def search_chunks(
        self, query: str, *, scope: SearchFilter, limit: int
    ) -> list[ChunkMatch]:
        """Keyword retrieval (§18) over the chunks of the scope's owner, best first.

        A chunk matches when it contains any of the query's ``keyword_terms``; the search
        engine behind it (full-text search in PostgreSQL) never crosses this port.
        """
        ...

    async def get_chunks(self, owner_id: UUID, chunk_ids: Sequence[UUID]) -> list[DocumentChunk]:
        """The owner's chunks among ``chunk_ids`` (retrieval evidence, §17); unknown or
        foreign ids are simply absent, so the caller can only ever read its own text."""
        ...

    async def delete_content(self, document_id: UUID) -> None: ...


class ConversationRepository(Protocol):
    async def add(self, conversation: Conversation) -> None: ...
    async def get(self, owner_id: UUID, conversation_id: UUID) -> Conversation | None: ...
    async def list_for_owner(self, owner_id: UUID) -> list[Conversation]: ...
    async def update(self, conversation: Conversation) -> None: ...
    async def delete(self, owner_id: UUID, conversation_id: UUID) -> bool: ...


class MessageRepository(Protocol):
    """Messages and their citations, always resolved through the conversation's owner."""

    async def add(
        self, owner_id: UUID, message: Message, citations: Sequence[Citation] = ()
    ) -> None:
        """Persist a message; raises ``NotFoundError`` unless the owner holds the conversation."""
        ...

    async def list_for_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> list[Message]: ...
    async def list_citations_for_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> dict[UUID, list[Citation]]:
        """Citations of every message in the conversation, keyed by message ID (one query)."""
        ...


class UnitOfWork(Protocol):
    @property
    def users(self) -> UserRepository: ...
    @property
    def refresh_tokens(self) -> RefreshTokenRepository: ...
    @property
    def collections(self) -> CollectionRepository: ...
    @property
    def documents(self) -> DocumentRepository: ...
    @property
    def document_content(self) -> DocumentContentRepository: ...
    @property
    def conversations(self) -> ConversationRepository: ...
    @property
    def messages(self) -> MessageRepository: ...

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
