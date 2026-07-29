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


def _chunk_row(document_id, user_id, chunk_index, content_hash):
    """Helper to construct a chunk row dict for testing."""
    return dict(
        document_id=document_id,
        user_id=user_id,
        chunk_index=chunk_index,
        content=f"content for {content_hash}",
        content_hash=content_hash,
        token_count=3,
        page_number=None,
        section=None,
        embedding=[0.0] * DIM,
    )


@pytest.mark.asyncio
async def test_get_hashes_for_document_maps_hash_to_chunk_id(db_session):
    user, document = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [_chunk_row(document.id, user.id, 0, "h1"), _chunk_row(document.id, user.id, 1, "h2")]
    )

    hashes = await repo.get_hashes_for_document(document.id)
    assert set(hashes.keys()) == {"h1", "h2"}


@pytest.mark.asyncio
async def test_delete_by_ids_removes_only_given_chunks(db_session):
    user, document = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [
            _chunk_row(document.id, user.id, 0, "h1"),
            _chunk_row(document.id, user.id, 1, "h2"),
            _chunk_row(document.id, user.id, 2, "h3"),
        ]
    )
    chunks = {c.content_hash: c.id for c in await repo.list()}

    await repo.delete_by_ids([chunks["h1"], chunks["h2"]])

    remaining = await repo.list()
    assert [c.id for c in remaining] == [chunks["h3"]]


@pytest.mark.asyncio
async def test_update_chunk_position_updates_index_page_and_section(db_session):
    user, document = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many([_chunk_row(document.id, user.id, 0, "h1")])
    chunk = (await repo.list())[0]

    await repo.update_chunk_position(
        chunk.id, chunk_index=5, page_number=9, section="New Section"
    )

    from app.models.document import DocumentChunk
    updated = await db_session.get(DocumentChunk, chunk.id)
    assert (updated.chunk_index, updated.page_number, updated.section) == (5, 9, "New Section")


@pytest.mark.asyncio
async def test_search_keyword_finds_exact_term_match(db_session):
    """BM25 keyword search: an exact word match ranks above an unrelated chunk."""
    user, doc = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many([
        dict(document_id=doc.id, user_id=user.id, chunk_index=0,
             content="photosynthesis converts sunlight into chemical energy",
             content_hash=hash_content("photosynthesis"), embedding=_vec(0)),
        dict(document_id=doc.id, user_id=user.id, chunk_index=1,
             content="the mitochondria is the powerhouse of the cell",
             content_hash=hash_content("mitochondria"), embedding=_vec(1)),
    ])
    await db_session.commit()

    results = await repo.search_keyword(user.id, "photosynthesis", top_k=5)
    assert len(results) == 1
    assert "photosynthesis" in results[0].content


@pytest.mark.asyncio
async def test_search_keyword_is_scoped_to_user(db_session):
    user_a, doc_a = await _user_and_doc(db_session)
    user_b, doc_b = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [dict(document_id=doc_b.id, user_id=user_b.id, chunk_index=0,
              content="quantum entanglement", content_hash=hash_content("quantum"),
              embedding=_vec(0))]
    )
    await db_session.commit()
    results = await repo.search_keyword(user_a.id, "quantum", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_keyword_filters_by_course(db_session):
    user, doc = await _user_and_doc(db_session, course="BIO")
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [dict(document_id=doc.id, user_id=user.id, chunk_index=0,
              content="cellular respiration", content_hash=hash_content("respiration"),
              embedding=_vec(0))]
    )
    await db_session.commit()
    assert len(await repo.search_keyword(user.id, "respiration", top_k=5, course="BIO")) == 1
    assert await repo.search_keyword(user.id, "respiration", top_k=5, course="MATH") == []


@pytest.mark.asyncio
async def test_search_keyword_no_match_returns_empty(db_session):
    user, doc = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [dict(document_id=doc.id, user_id=user.id, chunk_index=0,
              content="unrelated topic entirely", content_hash=hash_content("unrelated"),
              embedding=_vec(0))]
    )
    await db_session.commit()
    assert await repo.search_keyword(user.id, "nonexistentword", top_k=5) == []
