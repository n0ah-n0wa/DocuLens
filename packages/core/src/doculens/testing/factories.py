"""Factories producing valid domain entities with sensible defaults."""

from uuid import UUID

from doculens.domain.collections import Collection
from doculens.domain.conversations import Conversation
from doculens.domain.documents import Document, ProcessingStatus
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.domain.users import User, UserStatus


class Factories:
    @staticmethod
    def user(email: str = "owner@example.com") -> User:
        now = utc_now()
        return User(
            id=new_id(),
            email=email,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$placeholder",  # noqa: S106 - not a credential
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def collection(owner_id: UUID, name: str = "Contracts") -> Collection:
        now = utc_now()
        return Collection(
            id=new_id(),
            owner_id=owner_id,
            name=name,
            description=None,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def document(owner_id: UUID, collection_id: UUID | None = None) -> Document:
        now = utc_now()
        document_id = new_id()
        return Document(
            id=document_id,
            owner_id=owner_id,
            collection_id=collection_id,
            filename="report.pdf",
            storage_key=f"documents/{owner_id}/{document_id}/original.pdf",
            content_hash="c" * 64,
            mime_type="application/pdf",
            file_size=2048,
            processing_status=ProcessingStatus.UPLOADED,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def conversation(owner_id: UUID, collection_id: UUID | None = None) -> Conversation:
        now = utc_now()
        return Conversation(
            id=new_id(),
            owner_id=owner_id,
            collection_id=collection_id,
            title="Main risks",
            created_at=now,
            updated_at=now,
        )
