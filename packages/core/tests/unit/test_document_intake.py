"""Intake: validate, refuse duplicates and quota breaches, store the original, register the row."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from doculens.application.ingestion import DocumentIntakeService, UploadLimits
from doculens.domain.collections import CollectionNotFoundError
from doculens.domain.documents import ProcessingStatus
from doculens.domain.ingestion import (
    DocumentLimitReachedError,
    DuplicateDocumentError,
    EmptyUploadError,
    FileTooLargeError,
    InvalidFileSignatureError,
    UnsupportedFileTypeError,
)
from doculens.domain.storage import (
    PDF_MIME_TYPE,
    ObjectMetadata,
    StorageUnavailableError,
    content_hash,
)
from doculens.infrastructure.storage import FilesystemObjectStorage
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork
from doculens.testing.pdfs import pdf_with_pages

pytestmark = pytest.mark.unit

LIMITS = UploadLimits(
    max_file_size_bytes=64 * 1024, max_pages_per_document=5, max_documents_per_user=2
)


@pytest.fixture
def store() -> InMemoryStore:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    return store


@pytest.fixture
def owner_id(store: InMemoryStore) -> UUID:
    return next(iter(store.users))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "objects"


@pytest.fixture
def storage(root: Path) -> FilesystemObjectStorage:
    return FilesystemObjectStorage(root)


@pytest.fixture
def service(store: InMemoryStore, storage: FilesystemObjectStorage) -> DocumentIntakeService:
    return DocumentIntakeService(
        unit_of_work=lambda: InMemoryUnitOfWork(store), storage=storage, limits=LIMITS
    )


@pytest.fixture
def sample() -> bytes:
    return pdf_with_pages(["hello"])


def _stored_objects(root: Path) -> list[Path]:
    return sorted((root / "objects").rglob("original.pdf")) if root.exists() else []


async def test_a_valid_upload_is_stored_then_registered(
    service: DocumentIntakeService,
    store: InMemoryStore,
    storage: FilesystemObjectStorage,
    root: Path,
    owner_id: UUID,
    sample: bytes,
) -> None:
    document = await service.accept(
        owner_id, filename="  Q3 report.pdf ", declared_mime_type="application/pdf", data=sample
    )

    assert document.processing_status is ProcessingStatus.UPLOADED
    assert document.owner_id == owner_id
    assert document.filename == "Q3 report.pdf"
    assert document.content_hash == content_hash(sample)
    assert document.file_size == len(sample)
    assert document.mime_type == PDF_MIME_TYPE
    assert document.storage_key == f"documents/{owner_id}/{document.id}/original.pdf"
    assert store.documents[document.id] == document
    assert (await storage.get(document.storage_key)).data == sample
    assert len(_stored_objects(root)) == 1


@pytest.mark.parametrize(
    "case",
    [
        ("notes.txt", "application/pdf", b"%PDF-1.7 x", UnsupportedFileTypeError),
        ("notes.pdf", "image/png", b"%PDF-1.7 x", UnsupportedFileTypeError),
        ("notes.pdf", "application/pdf", b"", EmptyUploadError),
        ("notes.pdf", "application/pdf", b"%PDF-1.7 " + b"x" * (64 * 1024), FileTooLargeError),
        ("notes.pdf", "application/pdf", b"PK\x03\x04 not a pdf", InvalidFileSignatureError),
    ],
)
async def test_rejected_uploads_leave_nothing_behind(
    service: DocumentIntakeService,
    store: InMemoryStore,
    root: Path,
    owner_id: UUID,
    case: tuple[str, str, bytes, type[Exception]],
) -> None:
    filename, mime, data, error = case
    with pytest.raises(error):
        await service.accept(owner_id, filename=filename, declared_mime_type=mime, data=data)

    assert store.documents == {}
    assert _stored_objects(root) == []


async def test_identical_content_is_refused_with_the_existing_document(
    service: DocumentIntakeService, root: Path, owner_id: UUID, sample: bytes
) -> None:
    first = await service.accept(
        owner_id, filename="a.pdf", declared_mime_type="application/pdf", data=sample
    )

    with pytest.raises(DuplicateDocumentError) as excinfo:
        await service.accept(
            owner_id, filename="renamed.pdf", declared_mime_type="application/pdf", data=sample
        )

    assert excinfo.value.existing_document_id == first.id
    assert len(_stored_objects(root)) == 1


async def test_a_deleted_document_does_not_block_a_re_upload(
    service: DocumentIntakeService, store: InMemoryStore, owner_id: UUID, sample: bytes
) -> None:
    first = await service.accept(
        owner_id, filename="a.pdf", declared_mime_type="application/pdf", data=sample
    )
    store.documents[first.id] = replace(
        store.documents[first.id], processing_status=ProcessingStatus.DELETED
    )

    again = await service.accept(
        owner_id, filename="a.pdf", declared_mime_type="application/pdf", data=sample
    )

    assert again.id != first.id


async def test_the_same_content_may_be_uploaded_by_another_user(
    service: DocumentIntakeService, store: InMemoryStore, owner_id: UUID, sample: bytes
) -> None:
    other = Factories.user("other@example.com")
    store.users[other.id] = other
    await service.accept(
        owner_id, filename="a.pdf", declared_mime_type="application/pdf", data=sample
    )

    theirs = await service.accept(
        other.id, filename="a.pdf", declared_mime_type="application/pdf", data=sample
    )

    assert theirs.owner_id == other.id


async def test_the_per_user_document_limit_is_enforced(
    service: DocumentIntakeService, owner_id: UUID
) -> None:
    for index in range(LIMITS.max_documents_per_user):
        await service.accept(
            owner_id,
            filename=f"{index}.pdf",
            declared_mime_type="application/pdf",
            data=pdf_with_pages([f"document {index}"]),
        )

    with pytest.raises(DocumentLimitReachedError):
        await service.accept(
            owner_id,
            filename="one-too-many.pdf",
            declared_mime_type="application/pdf",
            data=pdf_with_pages(["one too many"]),
        )


async def test_a_foreign_or_unknown_collection_is_not_found(
    service: DocumentIntakeService, store: InMemoryStore, owner_id: UUID, sample: bytes
) -> None:
    other = Factories.user("other@example.com")
    store.users[other.id] = other
    theirs = Factories.collection(other.id)
    store.collections[theirs.id] = theirs

    with pytest.raises(CollectionNotFoundError):
        await service.accept(
            owner_id,
            filename="a.pdf",
            declared_mime_type="application/pdf",
            data=sample,
            collection_id=theirs.id,
        )
    assert store.documents == {}


async def test_a_failed_row_write_removes_the_stored_object(
    store: InMemoryStore,
    storage: FilesystemObjectStorage,
    root: Path,
    owner_id: UUID,
    sample: bytes,
) -> None:
    class BrokenUnitOfWork(InMemoryUnitOfWork):
        async def commit(self) -> None:
            message = "database gone"
            raise RuntimeError(message)

    service = DocumentIntakeService(
        unit_of_work=lambda: BrokenUnitOfWork(store), storage=storage, limits=LIMITS
    )

    with pytest.raises(RuntimeError, match="database gone"):
        await service.accept(
            owner_id, filename="a.pdf", declared_mime_type="application/pdf", data=sample
        )

    assert _stored_objects(root) == []


async def test_an_unavailable_store_fails_the_upload_before_any_row_exists(
    store: InMemoryStore, storage: FilesystemObjectStorage, owner_id: UUID, sample: bytes
) -> None:
    class DownStorage(FilesystemObjectStorage):
        async def put(
            self,
            key: str,
            data: bytes,
            *,
            content_type: str,
            metadata: Mapping[str, str] | None = None,
        ) -> ObjectMetadata:
            del key, data, content_type, metadata
            raise StorageUnavailableError

    service = DocumentIntakeService(
        unit_of_work=lambda: InMemoryUnitOfWork(store),
        storage=DownStorage(storage.root),
        limits=LIMITS,
    )

    with pytest.raises(StorageUnavailableError):
        await service.accept(
            owner_id, filename="a.pdf", declared_mime_type="application/pdf", data=sample
        )

    assert store.documents == {}
