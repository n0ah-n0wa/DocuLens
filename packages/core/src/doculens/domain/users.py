"""User entity (SPECIFICATIONS.md §7.1)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    email: str
    password_hash: str
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None
