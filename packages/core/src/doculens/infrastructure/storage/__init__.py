"""Object storage adapters and their composition helpers.

Composition roots call :func:`build_object_storage` with validated settings and get the adapter
the configuration selects; nothing else in the code base names a concrete backend.
"""

from typing import Protocol

from doculens.application.storage import ObjectStorage
from doculens.infrastructure.config import CoreSettings, StorageBackend
from doculens.infrastructure.storage.filesystem import FilesystemObjectStorage
from doculens.infrastructure.storage.s3 import S3ObjectStorage

PROBE_NAME = "object-storage"


class CheckableObjectStorage(ObjectStorage, Protocol):
    """An adapter that can also verify its backend is usable (for ``/health/ready``)."""

    async def check(self) -> None: ...


def build_object_storage(settings: CoreSettings) -> CheckableObjectStorage:
    if settings.storage_backend is StorageBackend.S3:
        return S3ObjectStorage.from_settings(settings)
    return FilesystemObjectStorage(
        settings.storage_local_root, max_object_bytes=settings.storage_max_object_bytes
    )


class ObjectStorageProbe:
    """Readiness probe for ``/health/ready``: the configured storage backend is usable."""

    name = PROBE_NAME

    def __init__(self, storage: CheckableObjectStorage) -> None:
        self._storage = storage

    async def check(self) -> None:
        await self._storage.check()


__all__ = [
    "PROBE_NAME",
    "CheckableObjectStorage",
    "FilesystemObjectStorage",
    "ObjectStorageProbe",
    "S3ObjectStorage",
    "build_object_storage",
]
