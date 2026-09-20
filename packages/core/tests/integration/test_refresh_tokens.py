"""Refresh-token records: durable, rotatable, revocable per family, removed with their user."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from doculens.domain.auth import RefreshToken
from doculens.domain.ids import new_id
from doculens.domain.time import utc_now
from doculens.infrastructure.persistence.database import Database
from doculens.infrastructure.persistence.models import RefreshTokenModel, UserModel
from doculens.testing.factories import Factories

pytestmark = pytest.mark.integration


def _token(user_id: object, family_id: object) -> RefreshToken:
    now = utc_now()
    return RefreshToken(
        id=new_id(),
        user_id=user_id,  # type: ignore[arg-type]  # helper keeps call sites short
        family_id=family_id,  # type: ignore[arg-type]
        issued_at=now,
        expires_at=now + timedelta(days=30),
    )


async def test_tokens_are_stored_rotated_and_revoked_by_family(
    database: Database, factories: type[Factories]
) -> None:
    user = factories.user()
    family = new_id()
    first, second, other_family = (
        _token(user.id, family),
        _token(user.id, family),
        _token(user.id, new_id()),
    )
    async with database.unit_of_work() as uow:
        await uow.users.add(user)
        for token in (first, second, other_family):
            await uow.refresh_tokens.add(token)
        assert await uow.refresh_tokens.rotate(first.id, second.id, now=utc_now())
        # A second rotation of the same token (replay or concurrent request) must not win.
        assert not await uow.refresh_tokens.rotate(first.id, new_id(), now=utc_now())
        assert not await uow.refresh_tokens.rotate(new_id(), new_id(), now=utc_now())
        await uow.commit()

    async with database.unit_of_work() as uow:
        stored_first = await uow.refresh_tokens.get(first.id)
        assert stored_first is not None
        assert stored_first.replaced_by_id == second.id
        assert stored_first.is_consumed

        assert await uow.refresh_tokens.revoke_family(family, now=utc_now()) == 1
        await uow.commit()

    async with database.unit_of_work() as uow:
        stored_second = await uow.refresh_tokens.get(second.id)
        untouched = await uow.refresh_tokens.get(other_family.id)
        assert stored_second is not None
        assert stored_second.revoked_at is not None
        assert untouched is not None
        assert untouched.revoked_at is None
        assert await uow.refresh_tokens.get(new_id()) is None


async def test_tokens_are_removed_with_their_user(
    database: Database, factories: type[Factories]
) -> None:
    user = factories.user()
    async with database.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.refresh_tokens.add(_token(user.id, new_id()))
        await uow.commit()

    async with database.session_factory() as session:
        row = await session.get(UserModel, user.id)
        assert row is not None
        await session.delete(row)
        await session.commit()

    async with database.session_factory() as session:
        assert (await session.scalars(select(RefreshTokenModel))).all() == []
