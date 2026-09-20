"""Object storage port (SPECIFICATIONS.md §9, §11, §61, §72).

Use cases store and fetch document originals through this port and never see a cloud SDK. The
contract every adapter honours:

- keys, content types and user metadata are validated with the domain rules before anything is
  stored, and objects larger than the configured maximum are refused on upload and on download;
- the SHA-256 of the content is computed on upload, recorded with the object and verified on
  download, so a corrupted or tampered object is reported instead of being processed;
- ``delete`` is idempotent and ``head`` answers ``None`` for a missing object, so callers can
  build idempotent, re-runnable workflows (§31);
- a backend that cannot be reached raises ``StorageUnavailableError`` so the API can fail safely
  (§67) instead of leaking provider errors.

Request handling never touches the raw port: :class:`OwnerScopedObjectStorage` binds a storage
to one owner and refuses every key outside that owner's prefix, so a document's bytes can only
be reached through a key the owner's own row produced (the same structural rule as the
owner-scoped repositories, §9).
"""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from doculens.domain.storage import (
    InvalidObjectKeyError,
    ObjectMetadata,
    ObjectNotFoundError,
    StoredObject,
    key_belongs_to,
)


class ObjectStorage(Protocol):
    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectMetadata:
        """Store ``data`` under ``key``, replacing any existing object, and describe the result."""
        ...

    async def get(self, key: str) -> StoredObject:
        """Read the object back, verifying its recorded hash; raise ``ObjectNotFoundError``."""
        ...

    async def head(self, key: str) -> ObjectMetadata | None:
        """Describe the object without reading its bytes; ``None`` when it does not exist."""
        ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove the object; a missing object is not an error."""
        ...


class OwnerScopedObjectStorage:
    """An ``ObjectStorage`` view limited to one owner's prefix.

    Reads, checks and deletes of a foreign key behave exactly like a missing object (nothing is
    disclosed, ADR-013); writing under a foreign key is a programming error and is refused.
    """

    def __init__(self, storage: ObjectStorage, owner_id: UUID) -> None:
        self._storage = storage
        self._owner_id = owner_id

    @property
    def owner_id(self) -> UUID:
        return self._owner_id

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectMetadata:
        if not key_belongs_to(key, self._owner_id):
            raise InvalidObjectKeyError
        return await self._storage.put(key, data, content_type=content_type, metadata=metadata)

    async def get(self, key: str) -> StoredObject:
        if not key_belongs_to(key, self._owner_id):
            raise ObjectNotFoundError
        return await self._storage.get(key)

    async def head(self, key: str) -> ObjectMetadata | None:
        if not key_belongs_to(key, self._owner_id):
            return None
        return await self._storage.head(key)

    async def exists(self, key: str) -> bool:
        if not key_belongs_to(key, self._owner_id):
            return False
        return await self._storage.exists(key)

    async def delete(self, key: str) -> None:
        if not key_belongs_to(key, self._owner_id):
            raise ObjectNotFoundError
        await self._storage.delete(key)
