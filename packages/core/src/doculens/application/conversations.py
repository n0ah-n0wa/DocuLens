"""Conversation use cases (SPECIFICATIONS.md §7.6 to §7.8, §9, §28).

Create, list, inspect, rename, delete and read the message history with its citations. Messages
are immutable and only ever read through their owner's conversation. Asking questions (creating
messages) is the RAG phase.
"""

from dataclasses import dataclass, replace
from uuid import UUID

from doculens.application.auth import Clock
from doculens.application.documents import ensure_collection_owned
from doculens.application.unit_of_work import UnitOfWorkFactory
from doculens.domain.common import UNSET, Unset, clean_label
from doculens.domain.conversations import (
    MAX_CONVERSATION_TITLE_LENGTH,
    Citation,
    Conversation,
    ConversationNotFoundError,
    Message,
)
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now


@dataclass(frozen=True, slots=True)
class MessageWithCitations:
    message: Message
    citations: list[Citation]


class ConversationService:
    def __init__(self, *, unit_of_work: UnitOfWorkFactory, clock: Clock = utc_now) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    async def create(
        self, owner_id: UUID, *, title: str, collection_id: UUID | None
    ) -> Conversation:
        now = self._clock()
        conversation = Conversation(
            id=new_id(),
            owner_id=owner_id,
            collection_id=collection_id,
            title=clean_label(title, field="title", max_length=MAX_CONVERSATION_TITLE_LENGTH),
            created_at=now,
            updated_at=now,
        )
        async with self._unit_of_work() as uow:
            if collection_id is not None:
                await ensure_collection_owned(uow, owner_id, collection_id)
            await uow.conversations.add(conversation)
            await uow.commit()
        return conversation

    async def list_for_owner(self, owner_id: UUID) -> list[Conversation]:
        async with self._unit_of_work() as uow:
            return await uow.conversations.list_for_owner(owner_id)

    async def get(self, owner_id: UUID, conversation_id: UUID) -> Conversation:
        async with self._unit_of_work() as uow:
            conversation = await uow.conversations.get(owner_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError
        return conversation

    async def update(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        *,
        title: str | Unset = UNSET,
        collection_id: UUID | Unset | None = UNSET,
    ) -> Conversation:
        async with self._unit_of_work() as uow:
            current = await uow.conversations.get(owner_id, conversation_id)
            if current is None:
                raise ConversationNotFoundError
            if isinstance(collection_id, Unset):
                target_collection = current.collection_id
            else:
                if collection_id is not None:
                    await ensure_collection_owned(uow, owner_id, collection_id)
                target_collection = collection_id
            updated = replace(
                current,
                title=(
                    current.title
                    if isinstance(title, Unset)
                    else clean_label(title, field="title", max_length=MAX_CONVERSATION_TITLE_LENGTH)
                ),
                collection_id=target_collection,
                updated_at=self._clock(),
            )
            await uow.conversations.update(updated)
            await uow.commit()
        return updated

    async def delete(self, owner_id: UUID, conversation_id: UUID) -> None:
        async with self._unit_of_work() as uow:
            if not await uow.conversations.delete(owner_id, conversation_id):
                raise ConversationNotFoundError
            await uow.commit()

    async def list_messages(
        self, owner_id: UUID, conversation_id: UUID
    ) -> list[MessageWithCitations]:
        async with self._unit_of_work() as uow:
            if await uow.conversations.get(owner_id, conversation_id) is None:
                raise ConversationNotFoundError
            messages = await uow.messages.list_for_conversation(owner_id, conversation_id)
            citations = await uow.messages.list_citations_for_conversation(
                owner_id, conversation_id
            )
        return [
            MessageWithCitations(message=message, citations=citations.get(message.id, []))
            for message in messages
        ]
