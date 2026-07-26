"""Tests for ChunkRepository.get_for_document (whole-document retrieval, owner-scoped)."""

import uuid

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.models.document import Document
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import hash_content

DIM = 1536


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot] = 1.0
    return v


async def _user_and_doc(
    session: AsyncSession, course: str | None = None
) -> tuple[User, Document]:
    user = await UserRepository(session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(session).create(
        user_id=user.id,
        filename="lecture.pdf",
        title="Lecture Notes",
        course=course,
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/lecture.pdf",
        file_size=1,
        chunk_count=0,
        embedding_model="gemini-embedding-001",
        embedding_dimension=DIM,
    )
    await session.commit()
    return user, doc


@pytest.mark.asyncio
async def test_get_for_document_returns_chunks_in_order(db_session: AsyncSession) -> None:
    """get_for_document returns all chunks of a document ordered by chunk_index."""
    user, doc = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)

    # Insert out-of-order to confirm ordering is enforced by the query.
    await repo.add_many(
        [
            dict(
                document_id=doc.id,
                user_id=user.id,
                chunk_index=2,
                content="third",
                content_hash=hash_content("third"),
                embedding=_vec(2),
            ),
            dict(
                document_id=doc.id,
                user_id=user.id,
                chunk_index=0,
                content="first",
                content_hash=hash_content("first"),
                embedding=_vec(0),
            ),
            dict(
                document_id=doc.id,
                user_id=user.id,
                chunk_index=1,
                content="second",
                content_hash=hash_content("second"),
                embedding=_vec(1),
            ),
        ]
    )
    await db_session.commit()

    chunks = await repo.get_for_document(doc.id, user.id)
    assert len(chunks) == 3
    assert [c.content for c in chunks] == ["first", "second", "third"]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


@pytest.mark.asyncio
async def test_get_for_document_non_owner_returns_empty(db_session: AsyncSession) -> None:
    """get_for_document returns [] when user_id doesn't match the document owner."""
    owner, doc = await _user_and_doc(db_session)
    other = await UserRepository(db_session).create(
        email=f"other-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    repo = ChunkRepository(db_session)

    await repo.add_many(
        [
            dict(
                document_id=doc.id,
                user_id=owner.id,
                chunk_index=0,
                content="secret",
                content_hash=hash_content("secret"),
                embedding=_vec(0),
            )
        ]
    )
    await db_session.commit()

    result = await repo.get_for_document(doc.id, other.id)
    assert result == []


@pytest.mark.asyncio
async def test_get_for_document_two_docs_isolated(db_session: AsyncSession) -> None:
    """Chunks from a different document are never returned."""
    user, doc_a = await _user_and_doc(db_session)
    _, doc_b = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)

    await repo.add_many(
        [
            dict(
                document_id=doc_a.id,
                user_id=user.id,
                chunk_index=0,
                content="doc-a chunk",
                content_hash=hash_content("doc-a chunk"),
                embedding=_vec(0),
            )
        ]
    )
    await repo.add_many(
        [
            dict(
                document_id=doc_b.id,
                user_id=user.id,
                chunk_index=0,
                content="doc-b chunk",
                content_hash=hash_content("doc-b chunk"),
                embedding=_vec(1),
            )
        ]
    )
    await db_session.commit()

    chunks = await repo.get_for_document(doc_a.id, user.id)
    assert len(chunks) == 1
    assert chunks[0].content == "doc-a chunk"
