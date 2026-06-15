import uuid

import pytest
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.models.user import User


async def _user(db_session) -> User:
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


def _doc_kwargs(user_id: uuid.UUID, **over: object) -> dict:
    base = dict(
        user_id=user_id,
        filename="a.pdf",
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/a",
        file_size=1,
        chunk_count=0,
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_create_and_list_for_user(db_session):
    user = await _user(db_session)
    repo = DocumentRepository(db_session)
    await repo.create(**_doc_kwargs(user.id, filename="a.pdf"))
    await repo.create(**_doc_kwargs(user.id, filename="b.pdf", course="BIO"))
    await db_session.commit()

    docs = await repo.list_for_user(user.id)
    assert {d.filename for d in docs} == {"a.pdf", "b.pdf"}

    bio = await repo.list_for_user(user.id, course="BIO")
    assert [d.filename for d in bio] == ["b.pdf"]


@pytest.mark.asyncio
async def test_get_by_user_and_hash(db_session):
    user = await _user(db_session)
    repo = DocumentRepository(db_session)
    h = uuid.uuid4().hex
    await repo.create(**_doc_kwargs(user.id, content_hash=h))
    await db_session.commit()
    assert await repo.get_by_user_and_hash(user.id, h) is not None
    assert await repo.get_by_user_and_hash(user.id, "nope") is None


@pytest.mark.asyncio
async def test_get_for_user_enforces_ownership(db_session):
    a = await _user(db_session)
    b = await _user(db_session)
    repo = DocumentRepository(db_session)
    doc = await repo.create(**_doc_kwargs(a.id))
    await db_session.commit()
    assert await repo.get_for_user(doc.id, a.id) is not None
    assert await repo.get_for_user(doc.id, b.id) is None
