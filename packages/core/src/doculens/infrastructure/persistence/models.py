"""ORM models: the relational schema for SPECIFICATIONS.md §7 with the integrity rules of §45.

Conventions
- Primary keys are application-generated UUIDs (``doculens.domain.ids``).
- Every timestamp is ``TIMESTAMPTZ``. ``created_at``/``updated_at`` are set by the application; the
  database supplies ``now()`` as a default for raw inserts and bumps ``updated_at`` on any update
  that does not set it explicitly, so a stale value cannot be persisted by accident.
- Enumerations are stored as constrained ``VARCHAR`` columns, not native enum types, so that
  adding a value is an ordinary migration.
- No ORM relationships are declared: every read is an explicit query through a repository, which
  rules out lazy loading under async sessions and accidental ORM-level cascades. Referential
  actions live in the database only.
- Foreign keys encode the deletion rules the specification fixes: owned content (pages, chunks,
  messages, citations) cascades with its parent; removing a collection detaches, never deletes, its
  documents and conversations (§30); a citation keeps its quoted text and survives the chunk being
  re-indexed (§23, §32). Deleting a document that is still cited is refused at the database level
  until `OQ-7` decides tombstoning; deleting a user is `OQ-24`. Every foreign-key column is the
  leading column of an index so referential actions never scan.
- Constraint names follow ``NAMING_CONVENTION`` so migrations are deterministic.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from doculens.domain.conversations import MessageRole
from doculens.domain.documents import ProcessingStatus
from doculens.domain.users import UserStatus

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


def _optional_timestamp() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), nullable=True)


def _enum(enum_type: type[UserStatus | ProcessingStatus | MessageRole], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=20,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Uniqueness is case-insensitive because the application always stores lower-case emails.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[UserStatus] = mapped_column(_enum(UserStatus, "user_status"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    last_login_at: Mapped[datetime | None] = _optional_timestamp()


class RefreshTokenModel(Base):
    """One row per issued refresh token, keyed by the JWT ``jti``; a family is one login session."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index(None, "user_id"),
        Index(None, "family_id"),
        Index(None, "expires_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    family_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = _optional_timestamp()
    replaced_by_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class CollectionModel(Base):
    __tablename__ = "collections"
    __table_args__ = (Index(None, "owner_id", "created_at"),)

    id: Mapped[UUID] = _uuid_pk()
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index(None, "owner_id", "created_at"),
        Index(None, "owner_id", "content_hash"),
        # One live document per (owner, content): duplicate uploads are refused (§49, OQ-6).
        Index(
            "uq_documents_owner_id_content_hash_active",
            "owner_id",
            "content_hash",
            unique=True,
            postgresql_where=sql_text("processing_status <> 'DELETED'"),
        ),
        Index(None, "collection_id"),
        Index(None, "processing_status", "updated_at"),
        CheckConstraint("file_size >= 0", name="file_size_non_negative"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_non_negative"),
        CheckConstraint("page_count IS NULL OR page_count >= 0", name="page_count_non_negative"),
    )

    id: Mapped[UUID] = _uuid_pk()
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    collection_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("collections.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        _enum(ProcessingStatus, "processing_status")
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    indexed_at: Mapped[datetime | None] = _optional_timestamp()
    document_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )


class DocumentPageModel(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number"),
        CheckConstraint("page_number >= 1", name="page_number_positive"),
        CheckConstraint("character_count >= 0", name="character_count_non_negative"),
    )

    id: Mapped[UUID] = _uuid_pk()
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    page_number: Mapped[int] = mapped_column(Integer)
    extracted_text: Mapped[str] = mapped_column(Text)
    character_count: Mapped[int] = mapped_column(Integer)
    page_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class DocumentChunkModel(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index(None, "page_id"),
        # Keyword retrieval (§18, OQ-4): full-text search over chunk text, language-agnostic.
        Index(
            "ix_document_chunks_text_search",
            sql_text("to_tsvector('simple'::regconfig, text)"),
            postgresql_using="gin",
        ),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
    )

    id: Mapped[UUID] = _uuid_pk()
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    page_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("document_pages.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    vector_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ConversationModel(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(None, "owner_id", "updated_at"),
        Index(None, "collection_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    collection_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("collections.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (Index(None, "conversation_id", "created_at"),)

    id: Mapped[UUID] = _uuid_pk()
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class CitationModel(Base):
    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint("message_id", "citation_order"),
        Index(None, "document_id"),
        Index(None, "chunk_id"),
        CheckConstraint("page_number >= 1", name="page_number_positive"),
        CheckConstraint("citation_order >= 0", name="citation_order_non_negative"),
    )

    id: Mapped[UUID] = _uuid_pk()
    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE")
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT")
    )
    page_number: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    quoted_text: Mapped[str] = mapped_column(Text)
    retrieval_score: Mapped[float] = mapped_column(Float)
    reranking_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    citation_order: Mapped[int] = mapped_column(Integer)
