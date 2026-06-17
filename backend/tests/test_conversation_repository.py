"""Tests for ConversationRepository (create / get_for_user / list_for_user / touch / delete)."""

import asyncio
import uuid

import pytest
from app.db.repositories.conversation import ConversationRepository
from app.db.repositories.user import UserRepository
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(session: AsyncSession) -> uuid.UUID:
    """Create a throwaway user and return its id."""
    repo = UserRepository(session)
    email = f"test-{uuid.uuid4().hex}@example.com"
    user = await repo.create(email=email, hashed_password="x")
    return user.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    repo = ConversationRepository(db_session)

    convo = await repo.create(user_id=user_id, title="My first chat")
    await db_session.commit()

    assert convo.id is not None
    assert convo.user_id == user_id
    assert convo.title == "My first chat"

    fetched = await repo.get_for_user(convo.id, user_id)
    assert fetched is not None
    assert fetched.id == convo.id


@pytest.mark.asyncio
async def test_get_for_user_wrong_owner(db_session: AsyncSession) -> None:
    """get_for_user must return None when the user_id doesn't match."""
    owner_id = await _make_user(db_session)
    other_id = await _make_user(db_session)
    repo = ConversationRepository(db_session)

    convo = await repo.create(user_id=owner_id, title="private")
    await db_session.commit()

    result = await repo.get_for_user(convo.id, other_id)
    assert result is None


@pytest.mark.asyncio
async def test_list_for_user_newest_first(db_session: AsyncSession) -> None:
    """list_for_user returns the calling user's conversations newest-first by updated_at."""
    user_id = await _make_user(db_session)
    other_id = await _make_user(db_session)
    repo = ConversationRepository(db_session)

    first = await repo.create(user_id=user_id, title="first")
    await db_session.commit()
    # Small sleep to ensure updated_at differs between the two inserts.
    await asyncio.sleep(0.05)
    second = await repo.create(user_id=user_id, title="second")
    await db_session.commit()

    # Noise: another user's conversation should NOT appear.
    await repo.create(user_id=other_id, title="other user")
    await db_session.commit()

    convos = await repo.list_for_user(user_id)
    assert len(convos) == 2
    # Newest (second) first.
    assert convos[0].id == second.id
    assert convos[1].id == first.id


@pytest.mark.asyncio
async def test_touch_bumps_updated_at(db_session: AsyncSession) -> None:
    """touch() must update updated_at so the conversation rises to the top of the list."""
    user_id = await _make_user(db_session)
    repo = ConversationRepository(db_session)

    first = await repo.create(user_id=user_id, title="first")
    await db_session.commit()
    await asyncio.sleep(0.05)
    second = await repo.create(user_id=user_id, title="second")
    await db_session.commit()

    # Without touch, second is the newest.
    convos_before = await repo.list_for_user(user_id)
    assert convos_before[0].id == second.id

    # Touch the older one so it becomes newest.
    await asyncio.sleep(0.05)
    await repo.touch(first.id)
    await db_session.commit()

    convos_after = await repo.list_for_user(user_id)
    assert convos_after[0].id == first.id


@pytest.mark.asyncio
async def test_delete(db_session: AsyncSession) -> None:
    """delete() removes the row; subsequent get returns None."""
    user_id = await _make_user(db_session)
    repo = ConversationRepository(db_session)

    convo = await repo.create(user_id=user_id, title="to delete")
    await db_session.commit()

    await repo.delete(convo.id)
    await db_session.commit()

    assert await repo.get_for_user(convo.id, user_id) is None
