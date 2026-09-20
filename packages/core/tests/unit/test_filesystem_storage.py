"""The filesystem adapter honours the storage contract and never leaves its root."""

import json
from pathlib import Path

import pytest

from doculens.application.storage import ObjectStorage
from doculens.domain.storage import (
    PDF_MIME_TYPE,
    ObjectIntegrityError,
    ObjectTooLargeError,
    StorageUnavailableError,
)
from doculens.infrastructure.storage import FilesystemObjectStorage, ObjectStorageProbe
from doculens.testing.storage_contract import PAYLOAD, ObjectStorageContract, unique_key

pytestmark = pytest.mark.unit


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "storage"


@pytest.fixture
def storage(root: Path) -> FilesystemObjectStorage:
    return FilesystemObjectStorage(root)


class TestFilesystemObjectStorage(ObjectStorageContract):
    async def corrupt_object(self, storage: ObjectStorage, key: str) -> None:
        assert isinstance(storage, FilesystemObjectStorage)
        target = storage.root / "objects" / Path(*key.split("/"))
        target.write_bytes(target.read_bytes() + b"tampered")


async def test_objects_and_metadata_are_laid_out_under_the_root(
    storage: FilesystemObjectStorage, root: Path
) -> None:
    key = unique_key()

    await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE, metadata={"k": "v"})

    object_file = root / "objects" / Path(*key.split("/"))
    metadata_file = root / "metadata" / Path(*key.split("/")).with_name("original.pdf.json")
    assert object_file.read_bytes() == PAYLOAD
    recorded = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert recorded["content_type"] == PDF_MIME_TYPE
    assert recorded["custom"] == {"k": "v"}
    assert len(recorded["content_hash"]) == 64
    assert not list(root.rglob("*.tmp-*")), "no temporary files left"  # noqa: ASYNC240 - test


async def test_a_replaced_hash_record_fails_integrity(
    storage: FilesystemObjectStorage, root: Path
) -> None:
    key = unique_key()
    await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE)
    metadata_file = root / "metadata" / Path(*key.split("/")).with_name("original.pdf.json")
    recorded = json.loads(metadata_file.read_text(encoding="utf-8"))
    recorded["content_hash"] = "0" * 64
    metadata_file.write_text(json.dumps(recorded), encoding="utf-8")

    with pytest.raises(ObjectIntegrityError):
        await storage.get(key)


async def test_the_readiness_probe_creates_the_root_and_verifies_it_is_writable(
    storage: FilesystemObjectStorage, root: Path
) -> None:
    probe = ObjectStorageProbe(storage)
    assert probe.name == "object-storage"

    await probe.check()

    assert (root / "objects").is_dir()
    assert (root / "metadata").is_dir()
    assert not list(root.glob(".probe-*"))  # noqa: ASYNC240 - test


async def test_delete_removes_metadata_first_and_prunes_empty_directories(
    storage: FilesystemObjectStorage, root: Path
) -> None:
    key = unique_key()
    await storage.put(key, PAYLOAD, content_type=PDF_MIME_TYPE)
    owner_dir = root / "objects" / Path(*key.split("/")[:2])

    await storage.delete(key)

    assert not owner_dir.exists(), "no per-owner or per-document directory lingers"
    assert (root / "objects").is_dir()
    assert (root / "metadata").is_dir()


async def test_objects_above_the_size_cap_are_refused_on_put_and_get(root: Path) -> None:
    storage = FilesystemObjectStorage(root, max_object_bytes=1024)
    key = unique_key()

    with pytest.raises(ObjectTooLargeError):
        await storage.put(key, b"x" * 1025, content_type=PDF_MIME_TYPE)
    assert await storage.exists(key) is False

    await storage.put(key, b"x" * 1024, content_type=PDF_MIME_TYPE)
    stricter = FilesystemObjectStorage(root, max_object_bytes=1024 - 1)
    with pytest.raises(ObjectTooLargeError):
        await stricter.get(key)


async def test_unexpected_os_failures_surface_as_storage_unavailable(
    storage: FilesystemObjectStorage,
) -> None:
    await storage.put("documents/a/b", PAYLOAD, content_type=PDF_MIME_TYPE)

    # "documents/a/b" is now a file, so nothing can be created beneath it.
    with pytest.raises(StorageUnavailableError):
        await storage.put("documents/a/b/c", PAYLOAD, content_type=PDF_MIME_TYPE)


def test_paths_can_never_resolve_outside_the_root(storage: FilesystemObjectStorage) -> None:
    # Key validation refuses traversal first; the resolver is the second line of defence.
    with pytest.raises(ValueError, match="outside the storage root"):
        storage._resolve_under(storage.root / "objects", "../../escape")  # noqa: SLF001
