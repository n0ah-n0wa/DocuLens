"""Collections (SPECIFICATIONS.md §30, §33). Every route acts as the authenticated user only."""

from datetime import datetime
from http import HTTPStatus
from typing import Self
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from doculens.domain.collections import (
    MAX_COLLECTION_DESCRIPTION_LENGTH,
    MAX_COLLECTION_NAME_LENGTH,
    Collection,
)
from doculens.domain.common import UNSET
from doculens_api.dependencies import CollectionServiceDep, CurrentUserDep
from doculens_api.errors import BAD_REQUEST_RESPONSE, BEARER_AUTH_RESPONSES, NOT_FOUND_RESPONSE

router = APIRouter(
    prefix="/api/v1/collections",
    tags=["collections"],
    responses=BEARER_AUTH_RESPONSES,
)


class CollectionResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_collection(cls, collection: Collection) -> Self:
        return cls(
            id=collection.id,
            name=collection.name,
            description=collection.description,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )


class CreateCollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_COLLECTION_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_COLLECTION_DESCRIPTION_LENGTH)


class UpdateCollectionRequest(BaseModel):
    """Fields left out are unchanged; ``description: null`` clears it."""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_COLLECTION_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_COLLECTION_DESCRIPTION_LENGTH)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Create a collection",
    responses=BAD_REQUEST_RESPONSE,
)
async def create_collection(
    body: CreateCollectionRequest, user: CurrentUserDep, collections: CollectionServiceDep
) -> CollectionResponse:
    collection = await collections.create(user.id, name=body.name, description=body.description)
    return CollectionResponse.from_collection(collection)


@router.get("", summary="List the user's collections")
async def list_collections(
    user: CurrentUserDep, collections: CollectionServiceDep
) -> list[CollectionResponse]:
    return [
        CollectionResponse.from_collection(c) for c in await collections.list_for_owner(user.id)
    ]


@router.get(
    "/{collection_id}",
    summary="Inspect a collection",
    responses=NOT_FOUND_RESPONSE,
)
async def get_collection(
    collection_id: UUID, user: CurrentUserDep, collections: CollectionServiceDep
) -> CollectionResponse:
    return CollectionResponse.from_collection(await collections.get(user.id, collection_id))


@router.patch(
    "/{collection_id}",
    summary="Rename or describe a collection",
    responses={**NOT_FOUND_RESPONSE, **BAD_REQUEST_RESPONSE},
)
async def update_collection(
    collection_id: UUID,
    body: UpdateCollectionRequest,
    user: CurrentUserDep,
    collections: CollectionServiceDep,
) -> CollectionResponse:
    collection = await collections.update(
        user.id,
        collection_id,
        name=body.name if body.name is not None else UNSET,
        description=body.description if "description" in body.model_fields_set else UNSET,
    )
    return CollectionResponse.from_collection(collection)


@router.delete(
    "/{collection_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Delete a collection (its documents and conversations are kept, detached)",
    responses=NOT_FOUND_RESPONSE,
)
async def delete_collection(
    collection_id: UUID, user: CurrentUserDep, collections: CollectionServiceDep
) -> Response:
    await collections.delete(user.id, collection_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)
