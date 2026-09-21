"""SQLAlchemy implementations of the repository ports.

Each repository is bound to the session of one unit of work. Reads of user-owned resources always
filter by ``owner_id`` (§9). ``update`` methods load the row and copy the entity's fields onto it,
so callers only ever handle domain entities.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Table, Text, delete, func, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from doculens.domain.auth import RefreshToken
from doculens.domain.collections import Collection
from doculens.domain.conversations import Citation, Conversation, Message
from doculens.domain.documents import Document, DocumentChunk, DocumentPage, ProcessingStatus
from doculens.domain.errors import ConflictError, NotFoundError
from doculens.domain.ingestion import DuplicateDocumentError
from doculens.domain.retrieval import ChunkMatch, keyword_terms
from doculens.domain.users import User
from doculens.domain.vectors import SearchFilter
from doculens.infrastructure.persistence import mappers
from doculens.infrastructure.persistence.models import (
    CitationModel,
    CollectionModel,
    ConversationModel,
    DocumentChunkModel,
    DocumentModel,
    DocumentPageModel,
    MessageModel,
    RefreshTokenModel,
    UserModel,
)


class SqlAlchemyRefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> None:
        self._session.add(mappers.refresh_token_to_row(token))
        await self._session.flush()

    async def get(self, token_id: UUID) -> RefreshToken | None:
        row = await self._session.get(RefreshTokenModel, token_id)
        return mappers.refresh_token_to_domain(row) if row is not None else None

    async def rotate(self, token_id: UUID, successor_id: UUID, *, now: datetime) -> bool:
        result = await self._session.execute(
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.id == token_id,
                RefreshTokenModel.revoked_at.is_(None),
                RefreshTokenModel.replaced_by_id.is_(None),
            )
            .values(replaced_by_id=successor_id, revoked_at=now)
        )
        await self._session.flush()
        return int(cast("CursorResult[Any]", result).rowcount or 0) == 1

    async def revoke_family(self, family_id: UUID, *, now: datetime) -> int:
        result = await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.family_id == family_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self._session.flush()
        return int(cast("CursorResult[Any]", result).rowcount or 0)


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(mappers.user_to_row(user))
        await self._session.flush()

    async def lock(self, user_id: UUID) -> None:
        await self._session.execute(
            select(UserModel.id).where(UserModel.id == user_id).with_for_update()
        )

    async def get(self, user_id: UUID) -> User | None:
        row = await self._session.get(UserModel, user_id)
        return mappers.user_to_domain(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        rows = await self._session.scalars(select(UserModel).where(UserModel.email == email))
        row = rows.first()
        return mappers.user_to_domain(row) if row is not None else None

    async def update(self, user: User) -> None:
        row = await self._session.get(UserModel, user.id)
        if row is None:
            raise NotFoundError
        mappers.apply_user(row, user)
        await self._session.flush()


class SqlAlchemyCollectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, collection: Collection) -> None:
        self._session.add(mappers.collection_to_row(collection))
        await self._session.flush()

    async def get(self, owner_id: UUID, collection_id: UUID) -> Collection | None:
        row = await self._owned(owner_id, collection_id)
        return mappers.collection_to_domain(row) if row is not None else None

    async def list_for_owner(self, owner_id: UUID) -> list[Collection]:
        rows = await self._session.scalars(
            select(CollectionModel)
            .where(CollectionModel.owner_id == owner_id)
            .order_by(CollectionModel.created_at, CollectionModel.id)
        )
        return [mappers.collection_to_domain(row) for row in rows]

    async def update(self, collection: Collection) -> None:
        row = await self._owned(collection.owner_id, collection.id)
        if row is None:
            raise NotFoundError
        mappers.apply_collection(row, collection)
        await self._session.flush()

    async def delete(self, owner_id: UUID, collection_id: UUID) -> bool:
        row = await self._owned(owner_id, collection_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def _owned(self, owner_id: UUID, collection_id: UUID) -> CollectionModel | None:
        rows = await self._session.scalars(
            select(CollectionModel).where(
                CollectionModel.id == collection_id, CollectionModel.owner_id == owner_id
            )
        )
        return rows.first()


ACTIVE_CONTENT_INDEX = "uq_documents_owner_id_content_hash_active"
PAGE_NUMBER_INDEX = "uq_document_pages_document_id_page_number"


class SqlAlchemyDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, document: Document) -> None:
        try:
            async with self._session.begin_nested():
                self._session.add(mappers.document_to_row(document))
                await self._session.flush()
        except IntegrityError as exc:
            if ACTIVE_CONTENT_INDEX not in str(exc.orig):
                raise
            existing = await self.find_by_content_hash(document.owner_id, document.content_hash)
            raise DuplicateDocumentError(existing.id if existing else None) from exc

    async def get_for_processing(self, document_id: UUID) -> Document | None:
        row = await self._session.get(DocumentModel, document_id)
        return mappers.document_to_domain(row) if row is not None else None

    async def find_by_content_hash(self, owner_id: UUID, content_hash: str) -> Document | None:
        rows = await self._session.scalars(
            select(DocumentModel)
            .where(
                DocumentModel.owner_id == owner_id,
                DocumentModel.content_hash == content_hash,
                DocumentModel.processing_status != ProcessingStatus.DELETED,
            )
            .order_by(DocumentModel.created_at)
            .limit(1)
        )
        row = rows.first()
        return mappers.document_to_domain(row) if row is not None else None

    async def compare_and_update(
        self, document: Document, *, expected_status: ProcessingStatus
    ) -> bool:
        result = await self._session.execute(
            update(DocumentModel)
            .where(
                DocumentModel.id == document.id,
                DocumentModel.processing_status == expected_status,
            )
            .values(**mappers.document_values(document))
        )
        await self._session.flush()
        return int(cast("CursorResult[Any]", result).rowcount or 0) == 1

    async def get(self, owner_id: UUID, document_id: UUID) -> Document | None:
        row = await self._owned(owner_id, document_id)
        return mappers.document_to_domain(row) if row is not None else None

    async def list_for_owner(self, owner_id: UUID) -> list[Document]:
        rows = await self._session.scalars(
            select(DocumentModel)
            .where(DocumentModel.owner_id == owner_id)
            .order_by(DocumentModel.created_at, DocumentModel.id)
        )
        return [mappers.document_to_domain(row) for row in rows]

    async def list_in_collection(self, owner_id: UUID, collection_id: UUID) -> list[Document]:
        rows = await self._session.scalars(
            select(DocumentModel)
            .where(DocumentModel.owner_id == owner_id, DocumentModel.collection_id == collection_id)
            .order_by(DocumentModel.created_at, DocumentModel.id)
        )
        return [mappers.document_to_domain(row) for row in rows]

    async def count_for_owner(self, owner_id: UUID) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(DocumentModel)
            .where(DocumentModel.owner_id == owner_id)
        )
        return int(count or 0)

    async def update(self, document: Document) -> None:
        row = await self._owned(document.owner_id, document.id)
        if row is None:
            raise NotFoundError
        mappers.apply_document(row, document)
        await self._session.flush()

    async def _owned(self, owner_id: UUID, document_id: UUID) -> DocumentModel | None:
        rows = await self._session.scalars(
            select(DocumentModel).where(
                DocumentModel.id == document_id, DocumentModel.owner_id == owner_id
            )
        )
        return rows.first()


class SqlAlchemyDocumentContentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_pages(self, pages: Sequence[DocumentPage]) -> None:
        try:
            async with self._session.begin_nested():
                self._session.add_all([mappers.page_to_row(page) for page in pages])
                await self._session.flush()
        except IntegrityError as exc:
            if PAGE_NUMBER_INDEX not in str(exc.orig):
                raise
            # Another extraction of the same document landed first (unique page numbers).
            raise ConflictError from exc

    async def add_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        self._session.add_all([mappers.chunk_to_row(chunk) for chunk in chunks])
        await self._session.flush()

    async def replace_chunks(self, document_id: UUID, chunks: Sequence[DocumentChunk]) -> None:
        # Core statements on the table: the JSONB column is "metadata", a name the ORM entity
        # reserves for its registry, so the mapped class cannot be used for these statements.
        table = cast("Table", DocumentChunkModel.__table__)
        keep = [chunk.id for chunk in chunks]
        stale = delete(table).where(table.c.document_id == document_id)
        if keep:
            stale = stale.where(table.c.id.not_in(keep))
        await self._session.execute(stale)
        for chunk in chunks:
            values = mappers.chunk_values(chunk)
            values["metadata"] = values.pop("chunk_metadata")
            statement = pg_insert(table).values(id=chunk.id, **values)
            await self._session.execute(
                statement.on_conflict_do_update(index_elements=[table.c.id], set_=values)
            )
        await self._session.flush()

    async def list_pages(self, owner_id: UUID, document_id: UUID) -> list[DocumentPage]:
        rows = await self._session.scalars(
            select(DocumentPageModel)
            .join(DocumentModel, DocumentModel.id == DocumentPageModel.document_id)
            .where(DocumentModel.owner_id == owner_id, DocumentModel.id == document_id)
            .order_by(DocumentPageModel.page_number)
        )
        return [mappers.page_to_domain(row) for row in rows]

    async def set_vector_ids(self, document_id: UUID, vector_ids: Mapping[UUID, str]) -> None:
        for chunk_id, vector_id in vector_ids.items():
            await self._session.execute(
                update(DocumentChunkModel)
                .where(
                    DocumentChunkModel.id == chunk_id,
                    DocumentChunkModel.document_id == document_id,
                )
                .values(vector_id=vector_id)
            )
        await self._session.flush()

    async def list_chunks(self, owner_id: UUID, document_id: UUID) -> list[DocumentChunk]:
        rows = await self._session.scalars(
            select(DocumentChunkModel)
            .join(DocumentModel, DocumentModel.id == DocumentChunkModel.document_id)
            .where(DocumentModel.owner_id == owner_id, DocumentModel.id == document_id)
            .order_by(DocumentChunkModel.chunk_index)
        )
        return [mappers.chunk_to_domain(row) for row in rows]

    async def search_chunks(
        self, query: str, *, scope: SearchFilter, limit: int
    ) -> list[ChunkMatch]:
        # PostgreSQL full-text search (OQ-4): the "simple" configuration is language-agnostic and
        # stemming-free and the terms are OR-ed; the match expression is the one the GIN index
        # on document_chunks was built with (migration 0004). Terms are plain words, so the
        # query string can never carry tsquery syntax. A chunk ranks by how many distinct query
        # terms it contains (a common word repeated five times must not beat three different
        # terms), with cover density as the tie-break, normalised into [0, 1).
        terms = keyword_terms(query)
        if not terms or limit < 1:
            return []
        tsquery = func.to_tsquery("simple", " | ".join(terms))
        vector = func.to_tsvector("simple", DocumentChunkModel.text)
        term = func.unnest(sql_cast(list(terms), ARRAY(Text))).column_valued("term")
        matched = (
            select(func.count())
            .where(vector.op("@@")(func.to_tsquery("simple", term)))
            .scalar_subquery()
        )
        rank = matched + func.ts_rank_cd(vector, tsquery, 32)
        statement = (
            select(DocumentChunkModel, rank)
            .join(DocumentModel, DocumentModel.id == DocumentChunkModel.document_id)
            .where(DocumentModel.owner_id == scope.owner_id, vector.op("@@")(tsquery))
        )
        if scope.document_ids is not None:
            statement = statement.where(DocumentModel.id.in_(list(scope.document_ids)))
        if scope.collection_id is not None:
            statement = statement.where(DocumentModel.collection_id == scope.collection_id)
        rows = await self._session.execute(
            statement.order_by(rank.desc(), DocumentChunkModel.id).limit(limit)
        )
        return [
            ChunkMatch(chunk=mappers.chunk_to_domain(row), score=float(score))
            for row, score in rows.tuples()
        ]

    async def get_chunks(self, owner_id: UUID, chunk_ids: Sequence[UUID]) -> list[DocumentChunk]:
        if not chunk_ids:
            return []
        rows = await self._session.scalars(
            select(DocumentChunkModel)
            .join(DocumentModel, DocumentModel.id == DocumentChunkModel.document_id)
            .where(DocumentModel.owner_id == owner_id, DocumentChunkModel.id.in_(list(chunk_ids)))
            .order_by(DocumentChunkModel.document_id, DocumentChunkModel.chunk_index)
        )
        return [mappers.chunk_to_domain(row) for row in rows]

    async def delete_content(self, document_id: UUID) -> None:
        await self._session.execute(
            delete(DocumentChunkModel).where(DocumentChunkModel.document_id == document_id)
        )
        await self._session.execute(
            delete(DocumentPageModel).where(DocumentPageModel.document_id == document_id)
        )
        await self._session.flush()


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, conversation: Conversation) -> None:
        self._session.add(mappers.conversation_to_row(conversation))
        await self._session.flush()

    async def get(self, owner_id: UUID, conversation_id: UUID) -> Conversation | None:
        row = await self._owned(owner_id, conversation_id)
        return mappers.conversation_to_domain(row) if row is not None else None

    async def list_for_owner(self, owner_id: UUID) -> list[Conversation]:
        rows = await self._session.scalars(
            select(ConversationModel)
            .where(ConversationModel.owner_id == owner_id)
            .order_by(ConversationModel.updated_at.desc(), ConversationModel.id)
        )
        return [mappers.conversation_to_domain(row) for row in rows]

    async def update(self, conversation: Conversation) -> None:
        row = await self._owned(conversation.owner_id, conversation.id)
        if row is None:
            raise NotFoundError
        mappers.apply_conversation(row, conversation)
        await self._session.flush()

    async def delete(self, owner_id: UUID, conversation_id: UUID) -> bool:
        row = await self._owned(owner_id, conversation_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def _owned(self, owner_id: UUID, conversation_id: UUID) -> ConversationModel | None:
        rows = await self._session.scalars(
            select(ConversationModel).where(
                ConversationModel.id == conversation_id, ConversationModel.owner_id == owner_id
            )
        )
        return rows.first()


class SqlAlchemyMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, owner_id: UUID, message: Message, citations: Sequence[Citation] = ()
    ) -> None:
        if not await self._owns_conversation(owner_id, message.conversation_id):
            raise NotFoundError
        # Citations reference the message by foreign key but not by ORM relationship, so the
        # message row must be flushed before the citation rows are inserted. A foreign-key
        # failure here means the conversation, a cited document or a cited chunk disappeared
        # while the answer was being produced: a conflict the caller may retry, never a 500.
        try:
            self._session.add(mappers.message_to_row(message))
            await self._session.flush()
            self._session.add_all([mappers.citation_to_row(citation) for citation in citations])
            await self._session.flush()
        except IntegrityError as exc:
            message_text = "the conversation or a cited source changed while answering; retry"
            raise ConflictError(message_text) from exc

    async def list_for_conversation(
        self, owner_id: UUID, conversation_id: UUID, *, limit: int | None = None
    ) -> list[Message]:
        statement = (
            select(MessageModel)
            .join(ConversationModel, ConversationModel.id == MessageModel.conversation_id)
            .where(ConversationModel.owner_id == owner_id, ConversationModel.id == conversation_id)
        )
        if limit is None:
            rows = list(
                await self._session.scalars(
                    statement.order_by(MessageModel.created_at, MessageModel.id)
                )
            )
        else:
            # The newest ``limit`` rows, then back into chronological order.
            newest = await self._session.scalars(
                statement.order_by(MessageModel.created_at.desc(), MessageModel.id.desc()).limit(
                    max(limit, 0)
                )
            )
            rows = list(newest)[::-1]
        return [mappers.message_to_domain(row) for row in rows]

    async def list_citations_for_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> dict[UUID, list[Citation]]:
        rows = await self._session.scalars(
            select(CitationModel)
            .join(MessageModel, MessageModel.id == CitationModel.message_id)
            .join(ConversationModel, ConversationModel.id == MessageModel.conversation_id)
            .where(ConversationModel.owner_id == owner_id, ConversationModel.id == conversation_id)
            .order_by(CitationModel.message_id, CitationModel.citation_order)
        )
        grouped: dict[UUID, list[Citation]] = {}
        for row in rows:
            grouped.setdefault(row.message_id, []).append(mappers.citation_to_domain(row))
        return grouped

    async def _owns_conversation(self, owner_id: UUID, conversation_id: UUID) -> bool:
        rows = await self._session.scalars(
            select(ConversationModel.id).where(
                ConversationModel.id == conversation_id, ConversationModel.owner_id == owner_id
            )
        )
        return rows.first() is not None
