import uuid

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from tests.conftest import hash_content

DIM = 1536


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot] = 1.0
    return v


async def _user_and_doc(db_session, course=None):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(
        user_id=user.id,
        filename="a.pdf",
        title="Lecture A",
        course=course,
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/a",
        file_size=1,
        chunk_count=0,
        embedding_model="gemini-embedding-001",
        embedding_dimension=DIM,
    )
    await db_session.commit()
    return user, doc


@pytest.mark.asyncio
async def test_add_many_and_search_orders_by_similarity(db_session):
    user, doc = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [
            dict(document_id=doc.id, user_id=user.id, chunk_index=0,
                 content="far", content_hash=hash_content("far"), embedding=_vec(5)),
            dict(document_id=doc.id, user_id=user.id, chunk_index=1,
                 content="near", content_hash=hash_content("near"), embedding=_vec(0)),
        ]
    )
    await db_session.commit()

    results = await repo.search_similar(user.id, _vec(0), top_k=2)
    assert [r.content for r in results] == ["near", "far"]
    assert results[0].filename == "a.pdf"
    assert results[0].title == "Lecture A"
    assert results[0].score >= results[1].score


@pytest.mark.asyncio
async def test_search_is_scoped_to_user(db_session):
    user_a, doc_a = await _user_and_doc(db_session)
    user_b, doc_b = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [dict(document_id=doc_b.id, user_id=user_b.id, chunk_index=0,
              content="b-only", content_hash=hash_content("b-only"), embedding=_vec(0))]
    )
    await db_session.commit()
    results = await repo.search_similar(user_a.id, _vec(0), top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_filters_by_course(db_session):
    user, doc = await _user_and_doc(db_session, course="BIO")
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [dict(document_id=doc.id, user_id=user.id, chunk_index=0,
              content="bio chunk", content_hash=hash_content("bio chunk"), embedding=_vec(0))]
    )
    await db_session.commit()
    assert len(await repo.search_similar(user.id, _vec(0), top_k=5, course="BIO")) == 1
    assert await repo.search_similar(user.id, _vec(0), top_k=5, course="MATH") == []
