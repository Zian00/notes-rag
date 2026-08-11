"""Tests for GroupRepository + the orphan-to-ungrouped FK behavior on delete."""

import uuid

import pytest
from app.db.repositories.conversation import ConversationRepository
from app.db.repositories.group import GroupRepository
from app.db.repositories.user import UserRepository
from app.models.document import Document
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(session: AsyncSession) -> uuid.UUID:
    repo = UserRepository(session)
    user = await repo.create(email=f"test-{uuid.uuid4().hex}@example.com", hashed_password="x")
    return user.id


async def _make_document(
    session: AsyncSession, user_id: uuid.UUID, group_id: uuid.UUID
) -> Document:
    """Insert a minimal ready document assigned to a group."""
    doc = Document(
        user_id=user_id,
        filename="notes.pdf",
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/notes.pdf",
        file_size=1,
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        group_id=group_id,
    )
    session.add(doc)
    await session.flush()
    return doc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    repo = GroupRepository(db_session)

    group = await repo.create(user_id=user_id, name="CS101")
    await db_session.commit()

    assert group.id is not None
    fetched = await repo.get_for_user(group.id, user_id)
    assert fetched is not None
    assert fetched.name == "CS101"


@pytest.mark.asyncio
async def test_get_for_user_wrong_owner(db_session: AsyncSession) -> None:
    owner_id = await _make_user(db_session)
    other_id = await _make_user(db_session)
    repo = GroupRepository(db_session)

    group = await repo.create(user_id=owner_id, name="private")
    await db_session.commit()

    assert await repo.get_for_user(group.id, other_id) is None


@pytest.mark.asyncio
async def test_get_by_name_is_case_insensitive(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    repo = GroupRepository(db_session)

    created = await repo.create(user_id=user_id, name="CS101")
    await db_session.commit()

    # Different casing + surrounding whitespace still resolves to the same group.
    found = await repo.get_by_name(user_id, "  cs101 ")
    assert found is not None
    assert found.id == created.id


@pytest.mark.asyncio
async def test_list_for_user_alphabetical_and_scoped(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    other_id = await _make_user(db_session)
    repo = GroupRepository(db_session)

    await repo.create(user_id=user_id, name="Math")
    await repo.create(user_id=user_id, name="algorithms")  # lowercase sorts before "Math"
    await repo.create(user_id=other_id, name="other-user")
    await db_session.commit()

    groups = await repo.list_for_user(user_id)
    assert [g.name for g in groups] == ["algorithms", "Math"]  # case-insensitive order, scoped


@pytest.mark.asyncio
async def test_rename(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    repo = GroupRepository(db_session)

    group = await repo.create(user_id=user_id, name="old")
    await db_session.commit()

    await repo.rename(group.id, "new")
    await db_session.commit()

    refetched = await repo.get_for_user(group.id, user_id)
    assert refetched is not None
    assert refetched.name == "new"


@pytest.mark.asyncio
async def test_case_insensitive_uniqueness_enforced(db_session: AsyncSession) -> None:
    """The functional unique index rejects a same-name-different-case duplicate."""
    from sqlalchemy.exc import IntegrityError

    user_id = await _make_user(db_session)
    repo = GroupRepository(db_session)

    await repo.create(user_id=user_id, name="CS101")
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await repo.create(user_id=user_id, name="cs101")
        await db_session.commit()


@pytest.mark.asyncio
async def test_delete_orphans_chats_and_documents_to_ungrouped(db_session: AsyncSession) -> None:
    """Deleting a group must NULL group_id on its chats/docs, not delete them."""
    user_id = await _make_user(db_session)
    groups = GroupRepository(db_session)
    convos = ConversationRepository(db_session)

    group = await groups.create(user_id=user_id, name="CS101")
    await db_session.commit()

    convo = await convos.create(user_id=user_id, title="chat in group")
    convo.group_id = group.id
    doc = await _make_document(db_session, user_id, group.id)
    await db_session.commit()

    await groups.delete(group.id)
    await db_session.commit()

    # Rows survive; their group link is cleared (orphan-to-ungrouped).
    surviving_convo = await convos.get_for_user(convo.id, user_id)
    assert surviving_convo is not None
    await db_session.refresh(surviving_convo)
    assert surviving_convo.group_id is None

    await db_session.refresh(doc)
    assert doc.group_id is None
