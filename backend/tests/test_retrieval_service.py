import uuid

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.services.retrieval import RetrievalService

from tests.fakes import FakeEmbeddingsProvider

DIM = 1536


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot] = 1.0
    return v


@pytest.mark.asyncio
async def test_search_embeds_query_and_returns_matches(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(
        user_id=user.id, filename="a.pdf", content_type="application/pdf",
        content_hash=uuid.uuid4().hex, storage_path="/tmp/a", file_size=1, chunk_count=1,
        embedding_model="m", embedding_dimension=DIM,
    )
    # FakeEmbeddingsProvider maps text -> one-hot at (len(text) % dim).
    await ChunkRepository(db_session).add_many(
        [dict(document_id=doc.id, user_id=user.id, chunk_index=0,
              content="abc", embedding=_vec(len("query!") % DIM))]
    )
    await db_session.commit()

    svc = RetrievalService(ChunkRepository(db_session), FakeEmbeddingsProvider(), default_top_k=5)
    results = await svc.search(user.id, "query!")
    assert len(results) == 1
    assert results[0].content == "abc"


@pytest.mark.asyncio
async def test_empty_query_returns_no_results(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    svc = RetrievalService(ChunkRepository(db_session), FakeEmbeddingsProvider(), default_top_k=5)
    assert await svc.search(user.id, "   ") == []
