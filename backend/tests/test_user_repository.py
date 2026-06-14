import uuid

import pytest
from app.db.repositories.user import UserRepository


@pytest.mark.asyncio
async def test_create_and_get_by_email(db_session):
    repo = UserRepository(db_session)
    email = f"user-{uuid.uuid4().hex}@example.com"

    created = await repo.create(email=email, hashed_password="x")
    await db_session.commit()

    fetched = await repo.get_by_email(email)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.email == email


@pytest.mark.asyncio
async def test_get_by_email_missing_returns_none(db_session):
    repo = UserRepository(db_session)
    assert await repo.get_by_email("nobody@example.com") is None
