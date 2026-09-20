"""Behaviour every ``ObjectStorage`` adapter must exhibit.

Test modules subclass :class:`ObjectStorageContract`, provide a ``storage`` fixture and implement
:meth:`corrupt_object` (tamper with a stored object behind the adapter's back). The same
assertions then run against the filesystem adapter in unit tests and against MinIO in
integration tests, so the two backends cannot drift apart.
"""

from uuid import uuid4

import pytest

from doculens.application.storage import ObjectStorage
from doculens.domain.storage import (
    PDF_MIME_TYPE,
    InvalidContentTypeError,
    InvalidObjectKeyError,
    InvalidObjectMetadataError,
    ObjectIntegrityError,
    ObjectNotFoundError,
    content_hash,
    document_object_key,
)

PAYLOAD = b"%PDF-1.7\n% DocuLens test object\n"


def unique_key() -> str:
    return document_object_key(uuid4(), uuid4())


class ObjectStorageContract:
    async def corrupt_object(self, storage: ObjectStorage, key: str) -> None:
        """Alter the stored bytes or the recorded hash without going through the adapter."""
        raise NotImplementedError

    async def test_put_then_get_round_trips_bytes_and_metadata(
        self, storage: ObjectStorage
    ) -> None:
        key = unique_key()

        stored = await storage.put(
            key, PAYLOAD, content_type=PDF_MIME_TYPE, metadata={"filename-hint": "report.pdf"}
        )
        fetched = await storage.get(key)

        assert stored.key == key
        assert stored.size == len(PAYLOAD)
        assert stored.content_hash == content_hash(PAYLOAD)
        assert fetched.data == PAYLOAD
        assert fetched.metadata.key == key
        assert fetched.metadata.size == len(PAYLOAD)
        assert fetched.metadata.content_type == PDF_MIME_TYPE
        assert fetched.metadata.content_hash == content_hash(PAYLOAD)
        assert dict(fetched.metadata.custom) == {"filename-hint": "report.pdf"}
        assert fetched.metadata.last_modified.tzinfo is not None

    async def test_head_and_exists_describe_without_downloading(
        self, storage: ObjectStorage
    ) -> None:
        key = unique_key()
        assert await storage.head(key) is None
        assert await storage.exists(key) is False

        await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE)

        described = await storage.head(key)
        assert described is not None
        assert described.size == len(PAYLOAD)
        assert described.content_hash == content_hash(PAYLOAD)
        assert described.content_type == PDF_MIME_TYPE
        assert await storage.exists(key) is True

    async def test_missing_objects_raise_not_found_on_get(self, storage: ObjectStorage) -> None:
        with pytest.raises(ObjectNotFoundError):
            await storage.get(unique_key())

    async def test_delete_is_idempotent(self, storage: ObjectStorage) -> None:
        key = unique_key()
        await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE)

        await storage.delete(key)
        await storage.delete(key)

        assert await storage.exists(key) is False
        with pytest.raises(ObjectNotFoundError):
            await storage.get(key)

    async def test_put_replaces_an_existing_object(self, storage: ObjectStorage) -> None:
        key = unique_key()
        await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE, metadata={"v": "1"})

        await storage.put(key, PAYLOAD * 2, content_type=PDF_MIME_TYPE, metadata={"v": "2"})

        fetched = await storage.get(key)
        assert fetched.data == PAYLOAD * 2
        assert dict(fetched.metadata.custom) == {"v": "2"}

    async def test_a_large_object_round_trips(self, storage: ObjectStorage) -> None:
        key = unique_key()
        payload = bytes(range(256)) * (6 * 1024 * 4)  # 6 MiB, above one multipart threshold

        await storage.put(key, payload, content_type=PDF_MIME_TYPE)

        fetched = await storage.get(key)
        assert fetched.data == payload
        assert fetched.metadata.size == len(payload)

    async def test_objects_are_isolated_by_key(self, storage: ObjectStorage) -> None:
        first, second = unique_key(), unique_key()
        await storage.put(first, b"first", content_type=PDF_MIME_TYPE)
        await storage.put(second, b"second", content_type=PDF_MIME_TYPE)

        await storage.delete(first)

        assert await storage.exists(first) is False
        assert (await storage.get(second)).data == b"second"

    async def test_tampered_objects_fail_the_integrity_check(self, storage: ObjectStorage) -> None:
        key = unique_key()
        await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE)

        await self.corrupt_object(storage, key)

        with pytest.raises(ObjectIntegrityError):
            await storage.get(key)

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "/documents/x",
            "documents//x",
            "documents/../etc/passwd",
            "documents/./x",
            "documents\\x",
            "documents/x y",
            "documents/é",
            "documents/x\n",
            "a" * 1025,
        ],
    )
    async def test_unsafe_keys_are_refused_before_any_io(
        self, storage: ObjectStorage, key: str
    ) -> None:
        with pytest.raises(InvalidObjectKeyError):
            await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE)
        with pytest.raises(InvalidObjectKeyError):
            await storage.get(key)
        with pytest.raises(InvalidObjectKeyError):
            await storage.head(key)
        with pytest.raises(InvalidObjectKeyError):
            await storage.delete(key)

    async def test_malformed_content_types_are_refused(self, storage: ObjectStorage) -> None:
        key = unique_key()
        for content_type in ("", "pdf", "application/pdf; x=y", "text/plain\r\nX: y"):
            with pytest.raises(InvalidContentTypeError):
                await storage.put(key, PAYLOAD, content_type=content_type)
        assert await storage.exists(key) is False

    async def test_reserved_or_unsafe_metadata_is_refused(self, storage: ObjectStorage) -> None:
        key = unique_key()
        for metadata in ({"sha256": "x"}, {"Upper": "x"}, {"ok": "café"}, {"ok": "a\r\nb"}):
            with pytest.raises(InvalidObjectMetadataError):
                await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE, metadata=metadata)
        assert await storage.exists(key) is False
