"""In-memory unit of work for tests that exercise use cases without PostgreSQL.

The repositories mirror the ownership and referential rules of the SQLAlchemy adapters (owner
filters on every read, detach-on-collection-delete, cascade-on-conversation-delete) so that use
cases behave the same way under both. Writes apply immediately to the shared ``InMemoryStore`` (no
transactional isolation); ``commits`` counts explicit commits.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from doculens.domain.auth import RefreshToken
from doculens.domain.collections import Collection
from doculens.domain.conversations import Citation, Conversation, Message
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.errors import ConflictError, NotFoundError
from doculens.domain.ingestion import DuplicateDocumentError
from doculens.domain.retrieval import ChunkMatch, keyword_terms, words_of
from doculens.domain.users import User
from doculens.domain.vectors import SearchFilter


class InMemoryStore:
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.refresh_tokens: dict[UUID, RefreshToken] = {}
        self.collections: dict[UUID, Collection] = {}
        self.documents: dict[UUID, Document] = {}
        self.pages: dict[UUID, DocumentPage] = {}
        self.chunks: dict[UUID, DocumentChunk] = {}
        self.conversations: dict[UUID, Conversation] = {}
        self.messages: dict[UUID, Message] = {}
        self.citations: dict[UUID, Citation] = {}
        self.commits = 0


class InMemoryUserRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def add(self, user: User) -> None:
        self._store.users[user.id] = user

    async def get(self, user_id: UUID) -> User | None:
        return self._store.users.get(user_id)

    async def lock(self, user_id: UUID) -> None:
        del user_id  # the in-memory store has no concurrent writers

    async def get_by_email(self, email: str) -> User | None:
        return next((user for user in self._store.users.values() if user.email == email), None)

    async def update(self, user: User) -> None:
        if user.id not in self._store.users:
            raise NotFoundError
        self._store.users[user.id] = user


class InMemoryRefreshTokenRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def add(self, token: RefreshToken) -> None:
        self._store.refresh_tokens[token.id] = token

    async def get(self, token_id: UUID) -> RefreshToken | None:
        return self._store.refresh_tokens.get(token_id)

    async def rotate(self, token_id: UUID, successor_id: UUID, *, now: datetime) -> bool:
        token = self._store.refresh_tokens.get(token_id)
        if token is None or token.is_consumed:
            return False
        self._store.refresh_tokens[token_id] = token.rotate_to(successor_id, now=now)
        return True

    async def revoke_family(self, family_id: UUID, *, now: datetime) -> int:
        revoked = 0
        for token_id, token in list(self._store.refresh_tokens.items()):
            if token.family_id == family_id and token.revoked_at is None:
                self._store.refresh_tokens[token_id] = token.revoke(now=now)
                revoked += 1
        return revoked


class InMemoryCollectionRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def add(self, collection: Collection) -> None:
        self._store.collections[collection.id] = collection

    async def get(self, owner_id: UUID, collection_id: UUID) -> Collection | None:
        collection = self._store.collections.get(collection_id)
        return collection if collection is not None and collection.owner_id == owner_id else None

    async def list_for_owner(self, owner_id: UUID) -> list[Collection]:
        owned = [c for c in self._store.collections.values() if c.owner_id == owner_id]
        return sorted(owned, key=lambda c: (c.created_at, c.id.hex))

    async def update(self, collection: Collection) -> None:
        if await self.get(collection.owner_id, collection.id) is None:
            raise NotFoundError
        self._store.collections[collection.id] = collection

    async def delete(self, owner_id: UUID, collection_id: UUID) -> bool:
        if await self.get(owner_id, collection_id) is None:
            return False
        del self._store.collections[collection_id]
        for document_id, document in list(self._store.documents.items()):
            if document.collection_id == collection_id:
                self._store.documents[document_id] = replace(document, collection_id=None)
        for conversation_id, conversation in list(self._store.conversations.items()):
            if conversation.collection_id == collection_id:
                self._store.conversations[conversation_id] = replace(
                    conversation, collection_id=None
                )
        return True


class InMemoryDocumentRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def add(self, document: Document) -> None:
        existing = await self.find_by_content_hash(document.owner_id, document.content_hash)
        if existing is not None:
            raise DuplicateDocumentError(existing.id)
        self._store.documents[document.id] = document

    async def get(self, owner_id: UUID, document_id: UUID) -> Document | None:
        document = self._store.documents.get(document_id)
        return document if document is not None and document.owner_id == owner_id else None

    async def get_for_processing(self, document_id: UUID) -> Document | None:
        return self._store.documents.get(document_id)

    async def find_by_content_hash(self, owner_id: UUID, content_hash: str) -> Document | None:
        matches = [
            d
            for d in self._store.documents.values()
            if d.owner_id == owner_id
            and d.content_hash == content_hash
            and d.processing_status is not ProcessingStatus.DELETED
        ]
        return min(matches, key=lambda d: (d.created_at, d.id.hex), default=None)

    async def compare_and_update(
        self, document: Document, *, expected_status: ProcessingStatus
    ) -> bool:
        current = self._store.documents.get(document.id)
        if current is None or current.processing_status is not expected_status:
            return False
        self._store.documents[document.id] = document
        return True

    async def list_for_owner(self, owner_id: UUID) -> list[Document]:
        owned = [
            d
            for d in self._store.documents.values()
            if d.owner_id == owner_id and d.processing_status is not ProcessingStatus.DELETED
        ]
        return sorted(owned, key=lambda d: (d.created_at, d.id.hex))

    async def list_in_collection(self, owner_id: UUID, collection_id: UUID) -> list[Document]:
        return [d for d in await self.list_for_owner(owner_id) if d.collection_id == collection_id]

    async def search_for_owner(
        self, owner_id: UUID, *, query: str, collection_id: UUID | None = None
    ) -> list[Document]:
        """Case-insensitive literal substring of the filename, like the SQL adapter's escaped
        ``ILIKE``: wildcards in ``query`` carry no special meaning."""
        needle = query.casefold()
        return [
            document
            for document in await self.list_for_owner(owner_id)
            if needle in document.filename.casefold()
            and (collection_id is None or document.collection_id == collection_id)
        ]

    async def count_for_owner(self, owner_id: UUID) -> int:
        return len(await self.list_for_owner(owner_id))

    async def update(self, document: Document) -> None:
        if await self.get(document.owner_id, document.id) is None:
            raise NotFoundError
        self._store.documents[document.id] = document


class InMemoryDocumentContentRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def add_pages(self, pages: Sequence[DocumentPage]) -> None:
        taken = {(p.document_id, p.page_number) for p in self._store.pages.values()}
        if any((page.document_id, page.page_number) in taken for page in pages):
            raise ConflictError
        for page in pages:
            self._store.pages[page.id] = page

    async def add_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        for chunk in chunks:
            self._store.chunks[chunk.id] = chunk

    async def replace_chunks(self, document_id: UUID, chunks: Sequence[DocumentChunk]) -> None:
        keep = {chunk.id for chunk in chunks}
        for chunk_id, chunk in list(self._store.chunks.items()):
            if chunk.document_id == document_id and chunk_id not in keep:
                del self._store.chunks[chunk_id]
                for citation_id, citation in list(self._store.citations.items()):
                    if citation.chunk_id == chunk_id:
                        self._store.citations[citation_id] = replace(citation, chunk_id=None)
        for chunk in chunks:
            self._store.chunks[chunk.id] = chunk

    def _owned(self, owner_id: UUID, document_id: UUID) -> bool:
        document = self._store.documents.get(document_id)
        return document is not None and document.owner_id == owner_id

    async def list_pages(self, owner_id: UUID, document_id: UUID) -> list[DocumentPage]:
        if not self._owned(owner_id, document_id):
            return []
        pages = [p for p in self._store.pages.values() if p.document_id == document_id]
        return sorted(pages, key=lambda p: p.page_number)

    async def set_vector_ids(self, document_id: UUID, vector_ids: Mapping[UUID, str]) -> None:
        for chunk_id, vector_id in vector_ids.items():
            chunk = self._store.chunks.get(chunk_id)
            if chunk is not None and chunk.document_id == document_id:
                self._store.chunks[chunk_id] = replace(chunk, vector_id=vector_id)

    async def list_chunks(self, owner_id: UUID, document_id: UUID) -> list[DocumentChunk]:
        if not self._owned(owner_id, document_id):
            return []
        chunks = [c for c in self._store.chunks.values() if c.document_id == document_id]
        return sorted(chunks, key=lambda c: c.chunk_index)

    async def search_chunks(
        self, query: str, *, scope: SearchFilter, limit: int
    ) -> list[ChunkMatch]:
        """The number of distinct query terms present in the chunk, like the SQL adapter
        (whose tie-break by occurrence density is not reproduced)."""
        terms = keyword_terms(query)
        matches: list[ChunkMatch] = []
        for chunk in self._store.chunks.values():
            document = self._store.documents.get(chunk.document_id)
            if document is None or document.owner_id != scope.owner_id:
                continue
            if scope.document_ids is not None and document.id not in scope.document_ids:
                continue
            if scope.collection_id is not None and document.collection_id != scope.collection_id:
                continue
            words = set(words_of(chunk.text))
            matched = sum(1 for term in terms if term in words)
            if matched:
                matches.append(ChunkMatch(chunk=chunk, score=float(matched)))
        matches.sort(key=lambda match: (-match.score, match.chunk.id.hex))
        return matches[: max(0, limit)]

    async def get_chunks(self, owner_id: UUID, chunk_ids: Sequence[UUID]) -> list[DocumentChunk]:
        wanted = set(chunk_ids)
        chunks = [
            c
            for c in self._store.chunks.values()
            if c.id in wanted and self._owned(owner_id, c.document_id)
        ]
        return sorted(chunks, key=lambda c: (c.document_id.hex, c.chunk_index))

    async def delete_content(self, document_id: UUID) -> None:
        for chunk_id, chunk in list(self._store.chunks.items()):
            if chunk.document_id == document_id:
                del self._store.chunks[chunk_id]
                for citation_id, citation in list(self._store.citations.items()):
                    if citation.chunk_id == chunk_id:
                        self._store.citations[citation_id] = replace(citation, chunk_id=None)
        for page_id, page in list(self._store.pages.items()):
            if page.document_id == document_id:
                del self._store.pages[page_id]


class InMemoryConversationRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def add(self, conversation: Conversation) -> None:
        self._store.conversations[conversation.id] = conversation

    async def get(self, owner_id: UUID, conversation_id: UUID) -> Conversation | None:
        conversation = self._store.conversations.get(conversation_id)
        if conversation is not None and conversation.owner_id == owner_id:
            return conversation
        return None

    async def list_for_owner(self, owner_id: UUID) -> list[Conversation]:
        owned = [c for c in self._store.conversations.values() if c.owner_id == owner_id]
        return sorted(owned, key=lambda c: (c.updated_at, c.id.hex), reverse=True)

    async def update(self, conversation: Conversation) -> None:
        if await self.get(conversation.owner_id, conversation.id) is None:
            raise NotFoundError
        self._store.conversations[conversation.id] = conversation

    async def delete(self, owner_id: UUID, conversation_id: UUID) -> bool:
        if await self.get(owner_id, conversation_id) is None:
            return False
        del self._store.conversations[conversation_id]
        for message_id, message in list(self._store.messages.items()):
            if message.conversation_id == conversation_id:
                del self._store.messages[message_id]
                for citation_id, citation in list(self._store.citations.items()):
                    if citation.message_id == message_id:
                        del self._store.citations[citation_id]
        return True


class InMemoryMessageRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def _owned(self, owner_id: UUID, conversation_id: UUID) -> bool:
        conversation = self._store.conversations.get(conversation_id)
        return conversation is not None and conversation.owner_id == owner_id

    async def add(
        self, owner_id: UUID, message: Message, citations: Sequence[Citation] = ()
    ) -> None:
        if not self._owned(owner_id, message.conversation_id):
            raise NotFoundError
        if message.id in self._store.messages:
            raise ConflictError  # like the primary key would
        self._store.messages[message.id] = message
        for citation in citations:
            self._store.citations[citation.id] = citation

    async def list_for_conversation(
        self, owner_id: UUID, conversation_id: UUID, *, limit: int | None = None
    ) -> list[Message]:
        if not self._owned(owner_id, conversation_id):
            return []
        messages = [
            m for m in self._store.messages.values() if m.conversation_id == conversation_id
        ]
        ordered = sorted(messages, key=lambda m: (m.created_at, m.id.hex))
        if limit is None:
            return ordered
        return ordered[-limit:] if limit > 0 else []

    async def list_citations_for_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> dict[UUID, list[Citation]]:
        grouped: dict[UUID, list[Citation]] = {}
        for message in await self.list_for_conversation(owner_id, conversation_id):
            cited = [c for c in self._store.citations.values() if c.message_id == message.id]
            if cited:
                grouped[message.id] = sorted(cited, key=lambda c: c.citation_order)
        return grouped


class InMemoryUnitOfWork:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self.users = InMemoryUserRepository(store)
        self.refresh_tokens = InMemoryRefreshTokenRepository(store)
        self.collections = InMemoryCollectionRepository(store)
        self.documents = InMemoryDocumentRepository(store)
        self.document_content = InMemoryDocumentContentRepository(store)
        self.conversations = InMemoryConversationRepository(store)
        self.messages = InMemoryMessageRepository(store)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self._store.commits += 1

    async def rollback(self) -> None:
        return None
