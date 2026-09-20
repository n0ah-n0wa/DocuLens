"""Collection entity: a logical grouping of documents (SPECIFICATIONS.md §7.2, §30)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from doculens.domain.errors import NotFoundError

MAX_COLLECTION_NAME_LENGTH = 200
MAX_COLLECTION_DESCRIPTION_LENGTH = 2000


class CollectionNotFoundError(NotFoundError):
    """Also raised for collections owned by someone else: existence is never disclosed (§9)."""

    code = "COLLECTION_NOT_FOUND"
    default_message = "The collection was not found."


@dataclass(frozen=True, slots=True)
class Collection:
    id: UUID
    owner_id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
