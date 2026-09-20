"""Tenant isolation at the storage port: a scoped storage never reaches a foreign key."""

from pathlib import Path
from uuid import uuid4

import pytest

from doculens.application.storage import OwnerScopedObjectStorage
from doculens.domain.storage import (
    PDF_MIME_TYPE,
    InvalidObjectKeyError,
    ObjectNotFoundError,
    document_object_key,
)
from doculens.infrastructure.storage import FilesystemObjectStorage

pytestmark = pytest.mark.unit

PAYLOAD = b"%PDF-1.7 owned"


@pytest.fixture
def backend(tmp_path: Path) -> FilesystemObjectStorage:
    return FilesystemObjectStorage(tmp_path / "storage")


async def test_an_owner_reaches_only_their_own_prefix(backend: FilesystemObjectStorage) -> None:
    alice, bob = uuid4(), uuid4()
    alice_key = document_object_key(alice, uuid4())
    bob_key = document_object_key(bob, uuid4())
    await backend.put(bob_key, b"bob's document", content_type=PDF_MIME_TYPE)
    scoped = OwnerScopedObjectStorage(backend, alice)

    stored = await scoped.put(alice_key, PAYLOAD, content_type=PDF_MIME_TYPE)
    assert stored.key == alice_key
    assert (await scoped.get(alice_key)).data == PAYLOAD
    assert await scoped.exists(alice_key) is True
    assert await scoped.head(alice_key) is not None
    assert scoped.owner_id == alice

    # Bob's object exists in the backend but is invisible and untouchable through Alice's view.
    with pytest.raises(ObjectNotFoundError):
        await scoped.get(bob_key)
    assert await scoped.head(bob_key) is None
    assert await scoped.exists(bob_key) is False
    with pytest.raises(ObjectNotFoundError):
        await scoped.delete(bob_key)
    assert (await backend.get(bob_key)).data == b"bob's document"

    await scoped.delete(alice_key)
    assert await backend.exists(alice_key) is False


@pytest.mark.parametrize(
    "key",
    [
        "documents/not-a-uuid/x/original.pdf",
        "other/{owner}/x/original.pdf",
        "documents/{owner}",
        "{owner}/documents/x/original.pdf",
    ],
)
async def test_writing_outside_the_owner_prefix_is_refused(
    backend: FilesystemObjectStorage, key: str
) -> None:
    owner = uuid4()
    scoped = OwnerScopedObjectStorage(backend, owner)

    with pytest.raises(InvalidObjectKeyError):
        await scoped.put(key.format(owner=owner), PAYLOAD, content_type=PDF_MIME_TYPE)
