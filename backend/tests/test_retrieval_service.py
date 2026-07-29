import uuid

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.services.retrieval import RetrievalService

from tests.conftest import hash_content
from tests.fakes import FakeEmbeddingsProvider, FakeReranker

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
              content="abc", content_hash=hash_content("abc"), embedding=_vec(len("query!") % DIM))]
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


def _doc_kwargs(user_id: uuid.UUID, chunk_count: int = 1) -> dict:
    return dict(
        user_id=user_id,
        filename="a.pdf",
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/a",
        file_size=1,
        chunk_count=chunk_count,
        embedding_model="m",
        embedding_dimension=DIM,
    )


@pytest.mark.asyncio
async def test_search_with_reranker_reorders_candidates(db_session):
    """Reranker result order overrides pgvector distance order."""
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(**_doc_kwargs(user.id, chunk_count=2))

    # FakeEmbeddingsProvider: one-hot at len(text) % DIM.
    # Query "xxxxx" (len=5) → slot 5 → closest to "abcde" (len=5, slot=5).
    # pgvector order: ["abcde", "abcd"] (slot-5 chunk first, slot-4 chunk second).
    # FakeReranker reverses → ["abcd", "abcde"].
    await ChunkRepository(db_session).add_many([
        dict(document_id=doc.id, user_id=user.id, chunk_index=0,
             content="abcd", content_hash=hash_content("abcd"),
             embedding=_vec(len("abcd") % DIM)),
        dict(document_id=doc.id, user_id=user.id, chunk_index=1,
             content="abcde", content_hash=hash_content("abcde"),
             embedding=_vec(len("abcde") % DIM)),
    ])
    await db_session.commit()

    svc = RetrievalService(
        ChunkRepository(db_session), FakeEmbeddingsProvider(),
        default_top_k=2, candidate_k=2, reranker=FakeReranker(),
    )
    results = await svc.search(user.id, "xxxxx")

    assert len(results) == 2
    # Reranker reversed pgvector order → previously-second chunk is now first.
    assert results[0].content == "abcd"
    assert results[1].content == "abcde"


@pytest.mark.asyncio
async def test_search_with_reranker_trims_candidates_to_top_k(db_session):
    """candidate_k > top_k: reranker sees all candidates, but only top_k are returned."""
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(**_doc_kwargs(user.id, chunk_count=3))

    await ChunkRepository(db_session).add_many([
        dict(document_id=doc.id, user_id=user.id, chunk_index=i,
             content=f"chunk{i}", content_hash=hash_content(f"chunk{i}"),
             embedding=_vec(i % DIM))
        for i in range(3)
    ])
    await db_session.commit()

    # candidate_k=3 fetches all 3; top_k=1 → only the top-ranked result is returned.
    svc = RetrievalService(
        ChunkRepository(db_session), FakeEmbeddingsProvider(),
        default_top_k=1, candidate_k=3, reranker=FakeReranker(),
    )
    results = await svc.search(user.id, "anything")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_reranker_overwrites_score_with_its_own_relevance_score(db_session):
    """Post-rerank, .score is the cross-encoder's score — not the original cosine value."""
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(**_doc_kwargs(user.id, chunk_count=2))
    await ChunkRepository(db_session).add_many([
        dict(document_id=doc.id, user_id=user.id, chunk_index=0,
             content="abcd", content_hash=hash_content("abcd"),
             embedding=_vec(len("abcd") % DIM)),
        dict(document_id=doc.id, user_id=user.id, chunk_index=1,
             content="abcde", content_hash=hash_content("abcde"),
             embedding=_vec(len("abcde") % DIM)),
    ])
    await db_session.commit()

    svc = RetrievalService(
        ChunkRepository(db_session), FakeEmbeddingsProvider(),
        default_top_k=2, candidate_k=2, reranker=FakeReranker(),
    )
    results = await svc.search(user.id, "xxxxx")

    # FakeReranker assigns descending scores 1.0, 0.5, ... by its own output order —
    # a value the original cosine similarity would never coincidentally produce.
    assert results[0].score == 1.0
    assert results[1].score == 0.5


@pytest.mark.asyncio
async def test_hybrid_search_finds_keyword_only_chunk_vector_search_would_miss(db_session):
    """A chunk vector search ranks low (or misses, given a small candidate pool)
    but that contains an exact keyword match is still surfaced via BM25."""
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(**_doc_kwargs(user.id, chunk_count=2))
    query = "photosynthesis"
    await ChunkRepository(db_session).add_many([
        # Top vector match (embedding slot derived from the query length), but
        # its content has nothing to do with the query.
        dict(document_id=doc.id, user_id=user.id, chunk_index=0,
             content="irrelevant filler text", content_hash=hash_content("filler"),
             embedding=_vec(len(query) % DIM)),
        # Far from the query in vector space (different embedding slot), but
        # contains the exact keyword.
        dict(document_id=doc.id, user_id=user.id, chunk_index=1,
             content="photosynthesis converts sunlight into energy",
             content_hash=hash_content("photosynthesis"), embedding=_vec((len(query) + 1) % DIM)),
    ])
    await db_session.commit()

    # candidate_k=1: vector search alone would return ONLY the filler chunk.
    svc = RetrievalService(
        ChunkRepository(db_session), FakeEmbeddingsProvider(),
        default_top_k=2, candidate_k=1, keyword_search=True,
    )
    results = await svc.search(user.id, query)

    contents = {r.content for r in results}
    assert "photosynthesis converts sunlight into energy" in contents


@pytest.mark.asyncio
async def test_hybrid_search_dedupes_chunk_found_by_both_paths(db_session):
    """A chunk matched by BOTH vector and keyword search appears only once."""
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(**_doc_kwargs(user.id, chunk_count=1))
    query = "photosynthesis"
    await ChunkRepository(db_session).add_many([
        dict(document_id=doc.id, user_id=user.id, chunk_index=0,
             content="photosynthesis converts sunlight into energy",
             content_hash=hash_content("photosynthesis"), embedding=_vec(len(query) % DIM)),
    ])
    await db_session.commit()

    svc = RetrievalService(
        ChunkRepository(db_session), FakeEmbeddingsProvider(),
        default_top_k=5, candidate_k=5, keyword_search=True,
    )
    results = await svc.search(user.id, query)

    assert len(results) == 1
