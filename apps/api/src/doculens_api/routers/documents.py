"""Document metadata and lifecycle (SPECIFICATIONS.md §29, §31, §32, §33).

Upload is direct multipart through the API (provisional OQ-3 / ADR-020). Storage keys and
content hashes are internal and never exposed.
"""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field

from doculens.application.ratelimit import enforce
from doculens.domain.common import UNSET
from doculens.domain.documents import MAX_FILENAME_LENGTH, Document, ProcessingStatus
from doculens.domain.ingestion import EmptyUploadError, FileTooLargeError
from doculens_api.dependencies import (
    CurrentUserDep,
    DocumentIntakeDep,
    DocumentServiceDep,
    RateLimiterDep,
    SettingsDep,
)
from doculens_api.errors import (
    BAD_REQUEST_RESPONSE,
    BEARER_AUTH_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    PAYLOAD_TOO_LARGE_RESPONSE,
    RATE_LIMITED_RESPONSE,
)

router = APIRouter(
    prefix="/api/v1/documents",
    tags=["documents"],
    responses=BEARER_AUTH_RESPONSES,
)

MEBIBYTE = 1024 * 1024

UPLOAD_RESPONSES = {
    **NOT_FOUND_RESPONSE,
    **BAD_REQUEST_RESPONSE,
    **CONFLICT_RESPONSE,
    **PAYLOAD_TOO_LARGE_RESPONSE,
    **RATE_LIMITED_RESPONSE,
}


class DocumentResponse(BaseModel):
    id: UUID
    collection_id: UUID | None
    filename: str
    mime_type: str
    file_size: int
    page_count: int | None
    processing_status: ProcessingStatus
    processing_error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None
    metadata: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_document(cls, document: Document) -> Self:
        return cls(
            id=document.id,
            collection_id=document.collection_id,
            filename=document.filename,
            mime_type=document.mime_type,
            file_size=document.file_size,
            page_count=document.page_count,
            processing_status=document.processing_status,
            processing_error=document.processing_error,
            chunk_count=document.chunk_count,
            created_at=document.created_at,
            updated_at=document.updated_at,
            indexed_at=document.indexed_at,
            metadata=dict(document.metadata),
        )


class UpdateDocumentRequest(BaseModel):
    """Fields left out are unchanged; ``collection_id: null`` removes it from its collection."""

    filename: str | None = Field(default=None, min_length=1, max_length=MAX_FILENAME_LENGTH)
    collection_id: UUID | None = None


async def _read_upload(file: UploadFile, *, max_bytes: int) -> bytes:
    """Read at most ``max_bytes``; one extra byte detects oversize without buffering more."""
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FileTooLargeError
    return data


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Upload a PDF document",
    description=(
        "Accepts a multipart PDF upload, validates it without parsing, stores the original, "
        "registers the document as `UPLOADED`, and enqueues asynchronous processing. "
        "Capped by `MAX_FILE_SIZE_MB` (provisional OQ-3 / ADR-020)."
    ),
    responses=UPLOAD_RESPONSES,
)
async def upload_document(
    user: CurrentUserDep,
    intake: DocumentIntakeDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    file: Annotated[UploadFile, File(description="PDF file to upload.")],
    collection_id: Annotated[
        UUID | None,
        Form(description="Optional collection that must belong to the caller."),
    ] = None,
) -> DocumentResponse:
    await enforce(
        limiter,
        f"upload:user:{user.id}",
        limit=settings.upload_rate_limit_attempts,
        window_seconds=settings.upload_rate_limit_window_seconds,
    )
    max_bytes = settings.max_file_size_mb * MEBIBYTE
    data = await _read_upload(file, max_bytes=max_bytes)
    if not data:
        raise EmptyUploadError
    filename = file.filename or ""
    declared_mime = file.content_type or ""
    document = await intake.accept(
        user.id,
        filename=filename,
        declared_mime_type=declared_mime,
        data=data,
        collection_id=collection_id,
    )
    return DocumentResponse.from_document(document)


@router.get(
    "",
    summary="List or search the user's documents, optionally within one collection",
    responses=NOT_FOUND_RESPONSE,
)
async def list_documents(
    user: CurrentUserDep,
    documents: DocumentServiceDep,
    collection_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[
        str | None,
        Query(
            max_length=MAX_FILENAME_LENGTH,
            description="Filename search: a literal, case-insensitive substring (§29).",
        ),
    ] = None,
) -> list[DocumentResponse]:
    listed = await documents.list_for_owner(user.id, collection_id=collection_id, query=q)
    return [DocumentResponse.from_document(d) for d in listed]


@router.get(
    "/{document_id}",
    summary="Inspect a document",
    responses=NOT_FOUND_RESPONSE,
)
async def get_document(
    document_id: UUID, user: CurrentUserDep, documents: DocumentServiceDep
) -> DocumentResponse:
    return DocumentResponse.from_document(await documents.get(user.id, document_id))


@router.patch(
    "/{document_id}",
    summary="Rename a document or move it",
    responses={**NOT_FOUND_RESPONSE, **BAD_REQUEST_RESPONSE},
)
async def update_document(
    document_id: UUID,
    body: UpdateDocumentRequest,
    user: CurrentUserDep,
    documents: DocumentServiceDep,
) -> DocumentResponse:
    document = await documents.update(
        user.id,
        document_id,
        filename=body.filename if body.filename is not None else UNSET,
        collection_id=body.collection_id if "collection_id" in body.model_fields_set else UNSET,
    )
    return DocumentResponse.from_document(document)


@router.delete(
    "/{document_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Delete a document and its stored content (idempotent)",
    responses=NOT_FOUND_RESPONSE,
)
async def delete_document(
    document_id: UUID, user: CurrentUserDep, documents: DocumentServiceDep
) -> Response:
    await documents.delete(user.id, document_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.post(
    "/{document_id}/reprocess",
    summary="Re-run the pipeline from the stored original",
    responses={**NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE},
)
async def reprocess_document(
    document_id: UUID, user: CurrentUserDep, documents: DocumentServiceDep
) -> DocumentResponse:
    return DocumentResponse.from_document(await documents.reprocess(user.id, document_id))


@router.post(
    "/{document_id}/reindex",
    summary="Re-chunk and re-embed from stored pages",
    responses={**NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE},
)
async def reindex_document(
    document_id: UUID, user: CurrentUserDep, documents: DocumentServiceDep
) -> DocumentResponse:
    return DocumentResponse.from_document(await documents.reindex(user.id, document_id))
