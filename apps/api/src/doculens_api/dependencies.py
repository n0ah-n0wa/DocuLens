"""Dependency injection for request handlers.

Everything a handler needs is assembled once by :func:`doculens_api.main.create_app` into an
immutable :class:`AppComponents` stored on that application instance. Handlers receive components
through ``Annotated`` aliases, so tests can build isolated applications with fakes and no
module-level state is shared between instances.
"""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from doculens.application.auth import AuthService
from doculens.application.collections import CollectionService
from doculens.application.conversations import ConversationService
from doculens.application.documents import DocumentService
from doculens.application.health import ReadinessService
from doculens.application.ratelimit import RateLimiter
from doculens.application.storage import ObjectStorage, OwnerScopedObjectStorage
from doculens.domain.errors import UnauthenticatedError
from doculens.domain.users import User
from doculens.infrastructure.persistence.database import Database
from doculens_api.settings import ApiSettings

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAccessToken",
    description="Access token from POST /api/v1/auth/login or /refresh.",
)


@dataclass(frozen=True, slots=True)
class AppComponents:
    settings: ApiSettings
    database: Database
    readiness: ReadinessService
    auth: AuthService
    collections: CollectionService
    documents: DocumentService
    conversations: ConversationService
    rate_limiter: RateLimiter
    object_storage: ObjectStorage


def components_of(request: Request) -> AppComponents:
    return cast("AppComponents", request.app.state.components)


def get_settings(request: Request) -> ApiSettings:
    return components_of(request).settings


def get_readiness_service(request: Request) -> ReadinessService:
    return components_of(request).readiness


def get_auth_service(request: Request) -> AuthService:
    return components_of(request).auth


def get_collection_service(request: Request) -> CollectionService:
    return components_of(request).collections


def get_document_service(request: Request) -> DocumentService:
    return components_of(request).documents


def get_conversation_service(request: Request) -> ConversationService:
    return components_of(request).conversations


def get_rate_limiter(request: Request) -> RateLimiter:
    return components_of(request).rate_limiter


def client_address(request: Request) -> str:
    """The peer address as seen by this process; proxy headers are trusted only once the
    hosting decision (`OQ-19`) fixes which proxy sits in front."""
    return request.client.host if request.client is not None else "unknown"


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Resolve the bearer access token to an active user, or answer 401 (§8, §9)."""
    if credentials is None or not credentials.credentials:
        raise UnauthenticatedError
    return await components_of(request).auth.authenticate(credentials.credentials)


SettingsDep = Annotated[ApiSettings, Depends(get_settings)]


async def get_owned_object_storage(
    request: Request, user: Annotated[User, Depends(get_current_user)]
) -> OwnerScopedObjectStorage:
    """Object storage limited to the authenticated user's prefix (§9, §11).

    The raw storage stays inside the composition root: handlers cannot reach a key that does not
    belong to the caller, whatever identifier a request carries.
    """
    return OwnerScopedObjectStorage(components_of(request).object_storage, user.id)


ReadinessDep = Annotated[ReadinessService, Depends(get_readiness_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CollectionServiceDep = Annotated[CollectionService, Depends(get_collection_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
OwnedObjectStorageDep = Annotated[OwnerScopedObjectStorage, Depends(get_owned_object_storage)]
ClientAddressDep = Annotated[str, Depends(client_address)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]
