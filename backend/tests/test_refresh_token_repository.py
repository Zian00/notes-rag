import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.repositories.refresh_token import RefreshTokenRepository
from app.db.repositories.user import UserRepository


async def _make_user(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@example.com", hashed_password="x"
    )
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_create_get_revoke(db_session):
    user = await _make_user(db_session)
    repo = RefreshTokenRepository(db_session)
    expires = datetime.now(tz=UTC) + timedelta(days=7)

    await repo.create(user_id=user.id, token_hash="hash-1", expires_at=expires)
    await db_session.commit()

    found = await repo.get_by_hash("hash-1")
    assert found is not None and found.revoked_at is None

    await repo.revoke(found)
    await db_session.commit()
    assert (await repo.get_by_hash("hash-1")).revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_all_for_user(db_session):
    user = await _make_user(db_session)
    repo = RefreshTokenRepository(db_session)
    now = datetime.now(tz=UTC)
    await repo.create(user_id=user.id, token_hash="h-a", expires_at=now + timedelta(days=7))
    await repo.create(user_id=user.id, token_hash="h-b", expires_at=now + timedelta(days=7))
    await db_session.commit()

    await repo.revoke_all_for_user(user.id)
    await db_session.commit()

    assert (await repo.get_by_hash("h-a")).revoked_at is not None
    assert (await repo.get_by_hash("h-b")).revoked_at is not None


@pytest.mark.asyncio
async def test_delete_expired_for_user(db_session):
    user = await _make_user(db_session)
    repo = RefreshTokenRepository(db_session)
    now = datetime.now(tz=UTC)
    await repo.create(user_id=user.id, token_hash="expired", expires_at=now - timedelta(days=1))
    await repo.create(user_id=user.id, token_hash="valid", expires_at=now + timedelta(days=1))
    await db_session.commit()

    await repo.delete_expired_for_user(user.id)
    await db_session.commit()

    assert await repo.get_by_hash("expired") is None
    assert await repo.get_by_hash("valid") is not None
