"""Filesystem adapter for the ``ObjectStorage`` port: local development without Docker or AWS.

Objects live under ``<root>/objects/<key>`` and their metadata (content type, hash, custom
entries, timestamp) under ``<root>/metadata/<key>.json``. Writes are atomic (temporary file plus
rename) so a crash never leaves a half-written object that ``head`` would report as complete;
deletes remove the metadata first so a half-deleted object is never reported as present, and
empty per-object directories are pruned so no identifiers linger on disk.

It honours the same contract as the S3 adapter, including hash verification on read and the
object-size cap, and is what unit tests of use cases run against. It is refused in staging and
production by ``CoreSettings``.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from doculens.domain.storage import (
    ObjectIntegrityError,
    ObjectMetadata,
    ObjectNotFoundError,
    StorageUnavailableError,
    StoredObject,
    content_hash,
    ensure_size_allowed,
    validate_content_type,
    validate_metadata,
    validate_object_key,
)

logger = logging.getLogger(__name__)

PROBE_NAME = "object-storage"
METADATA_SUFFIX = ".json"
DEFAULT_MAX_OBJECT_BYTES = 100 * 1024 * 1024


class FilesystemObjectStorage:
    name = PROBE_NAME

    def __init__(self, root: Path, *, max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES) -> None:
        self._root = root
        self._objects = root / "objects"
        self._metadata = root / "metadata"
        self._max_object_bytes = max_object_bytes

    @property
    def root(self) -> Path:
        return self._root

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectMetadata:
        validate_object_key(key)
        media_type = validate_content_type(content_type)
        custom = validate_metadata(metadata)
        ensure_size_allowed(len(data), self._max_object_bytes)
        described = ObjectMetadata(
            key=key,
            size=len(data),
            content_type=media_type,
            content_hash=content_hash(data),
            last_modified=datetime.now(UTC),
            custom=custom,
        )
        await self._run(lambda: self._write(key, data, described))
        logger.info(
            "object stored",
            extra={"operation": "storage.put", "object_key": key, "size": len(data)},
        )
        return described

    async def get(self, key: str) -> StoredObject:
        validate_object_key(key)
        metadata = await self._run(lambda: self._read_metadata(key))
        if metadata is None:
            raise ObjectNotFoundError
        ensure_size_allowed(metadata.size, self._max_object_bytes)
        try:
            data = await self._run(self._object_path(key).read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError from exc
        if metadata.content_hash != content_hash(data) or len(data) != metadata.size:
            logger.error(
                "stored object failed its integrity check",
                extra={"operation": "storage.integrity", "object_key": key},
            )
            raise ObjectIntegrityError
        return StoredObject(data=data, metadata=metadata)

    async def head(self, key: str) -> ObjectMetadata | None:
        validate_object_key(key)
        return await self._run(lambda: self._read_metadata(key))

    async def exists(self, key: str) -> bool:
        return await self.head(key) is not None

    async def delete(self, key: str) -> None:
        validate_object_key(key)

        def remove() -> None:
            metadata_path = self._metadata_path(key)
            object_path = self._object_path(key)
            metadata_path.unlink(missing_ok=True)
            object_path.unlink(missing_ok=True)
            # Only the object's own directory is pruned: a shared parent (the owner prefix) may
            # be receiving a concurrent write, and removing it would make that write fail.
            _prune_empty_directory(metadata_path.parent, stop_at=self._metadata)
            _prune_empty_directory(object_path.parent, stop_at=self._objects)

        await self._run(remove)
        logger.info("object deleted", extra={"operation": "storage.delete", "object_key": key})

    async def check(self) -> None:
        """Readiness: the root can be created and written to."""

        def probe() -> None:
            self._objects.mkdir(parents=True, exist_ok=True)
            self._metadata.mkdir(parents=True, exist_ok=True)
            marker = self._root / f".probe-{uuid4().hex}"
            marker.write_bytes(b"")
            marker.unlink()

        await self._run(probe)

    async def _run[T](self, operation: Callable[[], T]) -> T:
        """Run blocking IO on a worker thread; unexpected OS failures become storage errors."""
        try:
            return await asyncio.to_thread(operation)
        except FileNotFoundError:
            raise
        except OSError as exc:
            logger.warning(
                "filesystem object storage failed",
                extra={"operation": "storage.error", "error_type": type(exc).__name__},
            )
            raise StorageUnavailableError from exc

    # -- synchronous helpers (run on worker threads) -----------------------------------------

    def _object_path(self, key: str) -> Path:
        return self._resolve_under(self._objects, key)

    def _metadata_path(self, key: str) -> Path:
        return self._resolve_under(self._metadata, key + METADATA_SUFFIX)

    @staticmethod
    def _resolve_under(base: Path, relative: str) -> Path:
        """Defence in depth behind key validation, without touching the filesystem.

        Every segment must be a plain name: no empty, ``.`` or ``..`` segments, no separators
        and no drive or root markers, so the joined path is lexically inside ``base`` whatever
        the state of the directories (which may be created and pruned concurrently).
        """
        segments = relative.split("/")
        if any(_is_unsafe_segment(segment) for segment in segments):
            message = "object key resolves outside the storage root"
            raise ValueError(message)
        return base.joinpath(*segments)

    def _write(self, key: str, data: bytes, metadata: ObjectMetadata) -> None:
        object_path = self._object_path(key)
        metadata_path = self._metadata_path(key)
        _atomic_write(object_path, data)
        document = {
            "content_type": metadata.content_type,
            "content_hash": metadata.content_hash,
            "size": metadata.size,
            "last_modified": metadata.last_modified.isoformat(),
            "custom": dict(metadata.custom),
        }
        _atomic_write(metadata_path, json.dumps(document, sort_keys=True).encode("utf-8"))

    def _read_metadata(self, key: str) -> ObjectMetadata | None:
        try:
            raw = json.loads(self._metadata_path(key).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return ObjectMetadata(
            key=key,
            size=int(raw["size"]),
            content_type=str(raw["content_type"]),
            content_hash=raw.get("content_hash"),
            last_modified=datetime.fromisoformat(raw["last_modified"]),
            custom=dict(raw.get("custom", {})),
        )


_WRITE_ATTEMPTS = 3


def _is_unsafe_segment(segment: str) -> bool:
    return (
        segment in {"", ".", ".."}
        or "/" in segment
        or "\\" in segment
        or ":" in segment
        or Path(segment).is_absolute()
    )


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temporary file and rename, recreating a parent a concurrent delete pruned."""
    for attempt in range(_WRITE_ATTEMPTS):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp-{uuid4().hex}")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        except FileNotFoundError:
            if attempt == _WRITE_ATTEMPTS - 1:
                raise
        else:
            return
        finally:
            temporary.unlink(missing_ok=True)


def _prune_empty_directory(directory: Path, *, stop_at: Path) -> None:
    """Remove the object's own directory if it is now empty; best effort."""
    if directory == stop_at or not directory.is_relative_to(stop_at):
        return
    try:
        directory.rmdir()
    except OSError:
        return
