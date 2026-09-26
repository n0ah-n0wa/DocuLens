"""The authenticated user's own profile (SPECIFICATIONS.md §33, `GET /api/v1/users/me`)."""

from datetime import datetime
from typing import Self
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from doculens.domain.users import User, UserStatus
from doculens_api.dependencies import CurrentUserDep
from doculens_api.errors import BEARER_AUTH_RESPONSES

router = APIRouter(prefix="/api/v1/users", tags=["users"], responses=BEARER_AUTH_RESPONSES)


class UserResponse(BaseModel):
    """Public view of an account. The password hash never leaves the domain."""

    id: UUID
    email: str
    status: UserStatus
    created_at: datetime
    last_login_at: datetime | None = Field(default=None)

    @classmethod
    def from_user(cls, user: User) -> Self:
        return cls(
            id=user.id,
            email=user.email,
            status=user.status,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


@router.get("/me", summary="The authenticated user")
async def me(user: CurrentUserDep) -> UserResponse:
    return UserResponse.from_user(user)
