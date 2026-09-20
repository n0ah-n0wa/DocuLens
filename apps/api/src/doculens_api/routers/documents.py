"""Document metadata (SPECIFICATIONS.md §29, §33): list, inspect, rename, move.

Upload, reprocessing and deletion arrive with the ingestion phase. Storage keys and content
hashes are internal and never exposed.
"""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from doculens.domain.common import UNSET
from doculens.domain.documents import MAX_FILENAME_LENGTH, Document, ProcessingStatus
from doculens_api.dependencies import CurrentUserDep, DocumentServiceDep

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    HTTPStatus.NOT_FOUND: {"description": "No such document or collection for this user."}
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
        )


class UpdateDocumentRequest(BaseModel):
    """Fields left out are unchanged; ``collection_id: null`` removes it from its collection."""

    filename: str | None = Field(default=None, min_length=1, max_length=MAX_FILENAME_LENGTH)
    collection_id: UUID | None = None


@router.get("", summary="List the user's documents, optionally within one collection")
async def list_documents(
    user: CurrentUserDep,
    documents: DocumentServiceDep,
    collection_id: Annotated[UUID | None, Query()] = None,
) -> list[DocumentResponse]:
    listed = await documents.list_for_owner(user.id, collection_id=collection_id)
    return [DocumentResponse.from_document(d) for d in listed]


@router.get("/{document_id}", summary="Inspect a document", responses=NOT_FOUND)
async def get_document(
    document_id: UUID, user: CurrentUserDep, documents: DocumentServiceDep
) -> DocumentResponse:
    return DocumentResponse.from_document(await documents.get(user.id, document_id))


@router.patch("/{document_id}", summary="Rename a document or move it", responses=NOT_FOUND)
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
