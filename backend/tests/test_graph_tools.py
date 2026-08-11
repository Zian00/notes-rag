"""Tests for app/rag/graph/tools.py — user-scoped tool behaviour.

These tests spin up real DB sessions (via the shared _engine / conftest fixtures) to
verify that each tool correctly scopes its results to the requesting user and never
leaks another user's data.  A FakeEmbeddingsProvider is used so no Google API key is
needed.
"""

import uuid

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.models.document import Document
from app.models.user import User
from app.rag.graph.tools import build_tools, format_chunks_for_llm
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.conftest import hash_content
from tests.fakes import FakeEmbeddingsProvider

DIM = 1536


# ---------------------------------------------------------------------------
# Helpers — shared seeding utilities
# ---------------------------------------------------------------------------


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot % DIM] = 1.0
    return v


async def _seed_user_and_doc(
    session: AsyncSession, course: str | None = None
) -> tuple[User, Document]:
    """Create a user and one document, return both."""
    user = await UserRepository(session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(session).create(
        user_id=user.id,
        filename="notes.pdf",
        title="Lecture Notes",
        course=course,
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/notes.pdf",
        file_size=1,
        chunk_count=0,
        embedding_model="test",
        embedding_dimension=DIM,
    )
    await session.commit()
    return user, doc


async def _add_chunks(
    session: AsyncSession,
    doc: Document,
    user: User,
    texts: list[str],
) -> None:
    """Insert DocumentChunks for *doc*, flushing + committing."""
    repo = ChunkRepository(session)
    await repo.add_many(
        [
            {
                "document_id": doc.id,
                "user_id": user.id,
                "chunk_index": i,
                "content": t,
                "content_hash": hash_content(t),
                "embedding": _vec(i),
            }
            for i, t in enumerate(texts)
        ]
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests — retrieve_notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_notes_returns_caller_chunks(_engine: AsyncEngine) -> None:
    """retrieve_notes returns chunks belonging to the requesting user."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        user, doc = await _seed_user_and_doc(session)
        await _add_chunks(session, doc, user, ["alpha", "beta"])

    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    retrieve = next(t for t in tools if t.name == "retrieve_notes")

    config = {"configurable": {"user_id": user.id}}
    result = await retrieve.ainvoke({"query": "alpha"}, config=config)

    assert isinstance(result, list)
    assert len(result) >= 1
    contents = [r["content"] for r in result]
    assert any("alpha" in c or "beta" in c for c in contents)


@pytest.mark.asyncio
async def test_retrieve_notes_isolation(_engine: AsyncEngine) -> None:
    """retrieve_notes returns only the requesting user's chunks, never others'."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        user_a, doc_a = await _seed_user_and_doc(session)
        user_b, doc_b = await _seed_user_and_doc(session)
        await _add_chunks(session, doc_a, user_a, ["secret of user A"])
        await _add_chunks(session, doc_b, user_b, ["data of user B"])

    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    retrieve = next(t for t in tools if t.name == "retrieve_notes")

    # User A should only see their own data.
    config_a = {"configurable": {"user_id": user_a.id}}
    result_a = await retrieve.ainvoke({"query": "secret"}, config=config_a)
    contents_a = [r["content"] for r in result_a]
    assert all("user A" in c for c in contents_a)
    assert not any("user B" in c for c in contents_a)


# ---------------------------------------------------------------------------
# Tests — list_documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_documents_scoped(_engine: AsyncEngine) -> None:
    """list_documents returns only documents owned by the requesting user."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        user_a, doc_a = await _seed_user_and_doc(session, course="CS101")
        user_b, _doc_b = await _seed_user_and_doc(session)

    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    list_docs = next(t for t in tools if t.name == "list_documents")

    config_a = {"configurable": {"user_id": user_a.id}}
    result = await list_docs.ainvoke({}, config=config_a)

    assert isinstance(result, list)
    ids = [r["document_id"] for r in result]
    assert str(doc_a.id) in ids
    # User B's document must NOT appear.
    config_b = {"configurable": {"user_id": user_b.id}}
    result_b = await list_docs.ainvoke({}, config=config_b)
    b_ids = [r["document_id"] for r in result_b]
    assert str(doc_a.id) not in b_ids


# ---------------------------------------------------------------------------
# Tests — get_document_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_content_ordered(_engine: AsyncEngine) -> None:
    """get_document_content returns chunks in chunk_index order."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        user, doc = await _seed_user_and_doc(session)
        # Insert out of order.
        repo = ChunkRepository(session)
        await repo.add_many(
            [
                {"document_id": doc.id, "user_id": user.id, "chunk_index": 2, "content": "C",
                 "content_hash": hash_content("C"), "embedding": _vec(2)},
                {"document_id": doc.id, "user_id": user.id, "chunk_index": 0, "content": "A",
                 "content_hash": hash_content("A"), "embedding": _vec(0)},
                {"document_id": doc.id, "user_id": user.id, "chunk_index": 1, "content": "B",
                 "content_hash": hash_content("B"), "embedding": _vec(1)},
            ]
        )
        await session.commit()

    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    get_content = next(t for t in tools if t.name == "get_document_content")

    config = {"configurable": {"user_id": user.id}}
    result = await get_content.ainvoke({"document_id": str(doc.id)}, config=config)

    assert isinstance(result, list)
    assert len(result) == 3
    assert [r["content"] for r in result] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_get_document_content_non_owner_empty(_engine: AsyncEngine) -> None:
    """get_document_content returns [] when the user doesn't own the document."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        owner, doc = await _seed_user_and_doc(session)
        other = await UserRepository(session).create(
            email=f"other-{uuid.uuid4().hex}@e.com", hashed_password="x"
        )
        await ChunkRepository(session).add_many(
            [
                {
                    "document_id": doc.id,
                    "user_id": owner.id,
                    "chunk_index": 0,
                    "content": "private",
                    "content_hash": hash_content("private"),
                    "embedding": _vec(0),
                }
            ]
        )
        await session.commit()

    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    get_content = next(t for t in tools if t.name == "get_document_content")

    config = {"configurable": {"user_id": other.id}}
    result = await get_content.ainvoke({"document_id": str(doc.id)}, config=config)
    assert result == []


# ---------------------------------------------------------------------------
# Tests — format_chunks_for_llm helper
# ---------------------------------------------------------------------------


def test_format_chunks_empty() -> None:
    assert format_chunks_for_llm([]) == "NO RESULTS."


def test_format_chunks_numbered() -> None:
    chunks = [
        {"document_id": "doc-1", "title": "Lecture 1", "filename": "l1.pdf",
         "content": "hello", "page_number": 3, "section": None},
        {"document_id": "doc-2", "title": None, "filename": "l2.pdf",
         "content": "world", "page_number": None, "section": None},
    ]
    rendered = format_chunks_for_llm(chunks)
    assert "[1] Lecture 1 (p.3)" in rendered
    assert "[2] l2.pdf" in rendered
    assert "hello" in rendered
    assert "world" in rendered


def test_format_chunks_same_document_shares_number() -> None:
    """Two chunks from the same document must share a citation number — numbering
    is by document, not by chunk position, so an LLM-written "[n]" always matches
    the client-facing (document-deduped) citations array."""
    chunks = [
        {"document_id": "doc-1", "title": "Lecture 1", "filename": "l1.pdf",
         "content": "page one text", "page_number": 1, "section": None},
        {"document_id": "doc-2", "title": "Lecture 2", "filename": "l2.pdf",
         "content": "other doc text", "page_number": 1, "section": None},
        {"document_id": "doc-1", "title": "Lecture 1", "filename": "l1.pdf",
         "content": "page two text", "page_number": 2, "section": None},
    ]
    rendered = format_chunks_for_llm(chunks)
    assert "[1] Lecture 1 (p.1)\npage one text" in rendered
    assert "[2] Lecture 2 (p.1)\nother doc text" in rendered
    # Second doc-1 chunk reuses [1], NOT [3].
    assert "[1] Lecture 1 (p.2)\npage two text" in rendered
    assert "[3]" not in rendered
