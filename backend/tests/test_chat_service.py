"""Tests for ChatService (Task 12 — Milestone D).

The graph is compiled with FakeChatModel + InMemorySaver (no real LLM, no network,
no psycopg pool).  A real test DB sessionmaker is used so ConversationRepository
exercises actual SQL.

Scenarios:
1. New conversation — creates a row, yields meta/token/citations/done SSE frames.
2. Bad conversation_id (wrong user) — raises ConversationNotFound before streaming.
3. Reuse existing conversation — row is not re-created, updated_at is bumped.
4. list_conversations — returns the user's conversations newest-first.
5. get_detail — returns conversation row + message history from checkpointer.
6. delete_conversation — removes the row; get_detail raises ConversationNotFound.

SSE parsing helpers
-------------------
Each SSE frame is a string of the form::

    event: <event_name>\\ndata: <json>\\n\\n

``_collect_frames`` consumes the async generator and returns a list of
``(event_name, parsed_data)`` tuples so tests can assert on events by name.
"""

import asyncio
import json
import uuid
from typing import Any

import pytest
from app.core.config import Settings
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.rag.graph import build_rag_graph
from app.rag.graph.nodes import Grade
from app.services.chat import ChatService, ConversationNotFound
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.conftest import hash_content
from tests.fakes import FakeChatModel, FakeEmbeddingsProvider

DIM = 1536


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot % DIM] = 1.0
    return v


def _settings(**overrides: Any) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://notes:notes@localhost:5433/notes_rag_test",
        "jwt_secret": "test-secret",
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


async def _make_user(session: AsyncSession) -> Any:
    repo = UserRepository(session)
    email = f"svc-{uuid.uuid4().hex}@example.com"
    user = await repo.create(email=email, hashed_password="x")
    return user


async def _seed_doc(session: AsyncSession, user_id: uuid.UUID) -> Any:
    """Insert a document + one chunk so retrieve_notes can return real results."""
    from app.db.repositories.chunk import ChunkRepository

    doc = await DocumentRepository(session).create(
        user_id=user_id,
        filename="notes.pdf",
        title="Lecture Notes",
        course=None,
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/notes.pdf",
        file_size=1,
        chunk_count=0,
        embedding_model="test",
        embedding_dimension=DIM,
    )
    await ChunkRepository(session).add_many(
        [
            {
                "document_id": doc.id,
                "user_id": user_id,
                "chunk_index": 0,
                "content": "heap is a tree-based structure",
                "content_hash": hash_content("heap is a tree-based structure"),
                "embedding": _vec(0),
            }
        ]
    )
    await session.commit()
    return doc


def _retrieve_call(query: str, tc_id: str = "t1") -> AIMessage:
    """Convenience: AIMessage that triggers retrieve_notes (avoids long lines in tests)."""
    return AIMessage(
        content="",
        tool_calls=[{"name": "retrieve_notes", "args": {"query": query}, "id": tc_id}],
    )


def _build_service(model: FakeChatModel, maker: async_sessionmaker) -> ChatService:  # type: ignore[type-arg]
    """Compile the graph with fakes and wrap it in a ChatService."""
    s = _settings()
    graph = build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=s,
        checkpointer=InMemorySaver(),
    )
    return ChatService(graph, maker)


async def _collect_frames(gen: Any) -> list[tuple[str, Any]]:
    """Drain an SSE async generator and return parsed (event, data) pairs.

    Each frame is ``event: <name>\\ndata: <json>\\n\\n``.
    """
    frames: list[tuple[str, Any]] = []
    async for raw in gen:
        # Split on newlines; filter empty lines.
        lines = [ln for ln in raw.split("\n") if ln]
        event_name = None
        data_payload = None
        for line in lines:
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_payload = json.loads(line[len("data: "):])
        if event_name is not None:
            frames.append((event_name, data_payload))
    return frames


# ---------------------------------------------------------------------------
# 1. New conversation — creates row, yields meta/token/citations/done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_new_conversation(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    # agent → grade(relevant) → generate
    model = FakeChatModel(
        responses=[
            _retrieve_call("heap"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure [1]."),
        ]
    )
    svc = _build_service(model, maker)

    gen = svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    )
    frames = await _collect_frames(gen)

    event_names = [e for e, _ in frames]
    assert "meta" in event_names
    assert "token" in event_names
    assert "citations" in event_names
    assert "done" in event_names

    # meta frame must contain a conversation_id
    meta_data = next(d for e, d in frames if e == "meta")
    convo_id = uuid.UUID(meta_data["conversation_id"])  # must be parseable UUID

    # The conversation row must exist in the DB.
    async with maker() as s:
        from app.db.repositories.conversation import ConversationRepository
        row = await ConversationRepository(s).get_for_user(convo_id, user.id)
    assert row is not None
    assert row.title == "what is a heap?"


# ---------------------------------------------------------------------------
# 2. Bad conversation_id (other user) — raises ConversationNotFound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_wrong_owner_raises(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        owner = await _make_user(s)
        other = await _make_user(s)
        await s.commit()

    # Create a conversation as owner.
    svc = _build_service(
        FakeChatModel(
            responses=[
                _retrieve_call("x"),
                Grade(relevant=True, reason="context answers the question"),
                AIMessage("answer"),
            ]
        ),
        maker,
    )
    frames = await _collect_frames(svc.stream_answer(
        user_id=owner.id,
        conversation_id=None,
        question="hello",
    ))
    meta = next(d for e, d in frames if e == "meta")
    convo_id = uuid.UUID(meta["conversation_id"])

    # Now try to use that conversation_id as a different user.
    svc2 = _build_service(FakeChatModel(responses=[AIMessage("x")]), maker)
    with pytest.raises(ConversationNotFound):
        # The exception is raised before the generator yields anything;
        # we must materialise the generator to trigger the check.
        gen = svc2.stream_answer(
            user_id=other.id,
            conversation_id=convo_id,
            question="hi",
        )
        # Advance the generator; ownership check fires on first yield.
        async for _ in gen:
            pass


# ---------------------------------------------------------------------------
# 3. Reuse existing conversation — row is not duplicated, updated_at bumped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_reuses_existing_conversation(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    # First turn: create a new conversation.
    model1 = FakeChatModel(
        responses=[
            _retrieve_call("heap", "t1"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("answer 1"),
        ]
    )
    # Use a shared InMemorySaver so turn 2 can resume the same thread.
    checkpointer = InMemorySaver()
    settings = _settings()
    graph = build_rag_graph(
        chat_model=model1,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=settings,
        checkpointer=checkpointer,
    )
    svc = ChatService(graph, maker)

    frames1 = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames1 if e == "meta")["conversation_id"])

    async with maker() as s:
        from app.db.repositories.conversation import ConversationRepository
        row_before = await ConversationRepository(s).get_for_user(convo_id, user.id)
    assert row_before is not None
    updated_before = row_before.updated_at

    # Small delay so updated_at can differ.
    await asyncio.sleep(0.05)

    # Second turn: re-use the same conversation_id.
    # Swap in a fresh model with responses for turn 2. Prior history now exists, so
    # condense makes an LLM call first — its response is echoed back unchanged.
    model2 = FakeChatModel(
        responses=[
            AIMessage("and what about a min-heap?"),  # condense
            _retrieve_call("heap", "t2"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("answer 2"),
        ]
    )
    graph2 = build_rag_graph(
        chat_model=model2,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=settings,
        checkpointer=checkpointer,  # same checkpointer = same thread
    )
    svc2 = ChatService(graph2, maker)

    frames2 = await _collect_frames(svc2.stream_answer(
        user_id=user.id,
        conversation_id=convo_id,
        question="and what about a min-heap?",
    ))
    meta2 = next(d for e, d in frames2 if e == "meta")
    assert uuid.UUID(meta2["conversation_id"]) == convo_id  # same id

    # updated_at must be bumped.
    async with maker() as s:
        row_after = await ConversationRepository(s).get_for_user(convo_id, user.id)
    assert row_after is not None
    assert row_after.updated_at > updated_before  # type: ignore[operator]

    # Only one row for this user (no duplicate creation).
    async with maker() as s:
        from app.db.repositories.conversation import ConversationRepository
        all_convos = await ConversationRepository(s).list_for_user(user.id)
    user_convos = [c for c in all_convos if c.id == convo_id]
    assert len(user_convos) == 1


# ---------------------------------------------------------------------------
# 4. list_conversations — newest-first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_newest_first(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    checkpointer = InMemorySaver()
    settings = _settings()

    async def _make_convo(question: str) -> uuid.UUID:
        model = FakeChatModel(
            responses=[
                _retrieve_call(question),
                Grade(relevant=True, reason="context answers the question"),
                AIMessage("answer"),
            ]
        )
        graph = build_rag_graph(
            chat_model=model,
            embeddings=FakeEmbeddingsProvider(DIM),
            sessionmaker=maker,
            settings=settings,
            checkpointer=checkpointer,
        )
        svc = ChatService(graph, maker)
        frames = await _collect_frames(svc.stream_answer(
            user_id=user.id,
            conversation_id=None,
            question=question,
        ))
        return uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])

    id1 = await _make_convo("first question")
    await asyncio.sleep(0.05)
    id2 = await _make_convo("second question")

    # list_conversations uses the same graph (checkpointer not needed here).
    model = FakeChatModel(responses=[])
    graph = build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=settings,
        checkpointer=checkpointer,
    )
    svc = ChatService(graph, maker)
    convos = await svc.list_conversations(user.id)

    ids = [c.id for c in convos]
    assert id2 in ids
    assert id1 in ids
    # Newest-first: id2 was created last.
    assert ids.index(id2) < ids.index(id1)


# ---------------------------------------------------------------------------
# 5. get_detail — returns conversation + message history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_detail_returns_history(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    checkpointer = InMemorySaver()
    settings = _settings()
    model = FakeChatModel(
        responses=[
            _retrieve_call("heap"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure."),
        ]
    )
    graph = build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=settings,
        checkpointer=checkpointer,
    )
    svc = ChatService(graph, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])

    detail = await svc.get_detail(convo_id, user.id)
    assert detail["conversation"].id == convo_id

    messages = detail["messages"]
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles

    # The user's question must appear.
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert any("heap" in m["content"].lower() for m in user_msgs)

    # The assistant's answer must appear.
    ai_msgs = [m for m in messages if m["role"] == "assistant"]
    assert any("heap" in m["content"].lower() for m in ai_msgs)


@pytest.mark.asyncio
async def test_get_detail_no_synthetic_messages_after_rewrite_agentic(
    _engine: AsyncEngine,
) -> None:
    """A rewrite firing mid-turn must not leak a synthetic 'user' message into
    GET /conversations/{id} history — regression test for the bug where rewrite
    used to persist HumanMessage(new_q) into the conversation transcript."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    # agent → retrieve → grade(False) → rewrite → agent → retrieve → grade(True) → generate
    model = FakeChatModel(
        responses=[
            _retrieve_call("heap"),
            Grade(relevant=False, reason="off-topic"),
            AIMessage("better search query"),  # rewrite
            _retrieve_call("better search query", "t2"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure."),
        ]
    )
    svc = _build_service(model, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])

    detail = await svc.get_detail(convo_id, user.id)
    messages = detail["messages"]
    user_msgs = [m for m in messages if m["role"] == "user"]

    assert len(user_msgs) == 1, f"Expected exactly 1 user message, got: {user_msgs}"
    assert user_msgs[0]["content"] == "what is a heap?"


@pytest.mark.asyncio
async def test_get_detail_no_synthetic_messages_after_rewrite_linear(
    _engine: AsyncEngine,
) -> None:
    """Same regression, linear path (agentic_retrieval=False, no agent tool-call step)."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    # force_retrieve → grade(False) → rewrite → force_retrieve → grade(True) → generate
    model = FakeChatModel(
        responses=[
            Grade(relevant=False, reason="off-topic"),
            AIMessage("better search query"),  # rewrite
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure."),
        ]
    )
    graph = build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=_settings(agentic_retrieval=False),
        checkpointer=InMemorySaver(),
    )
    svc = ChatService(graph, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])

    detail = await svc.get_detail(convo_id, user.id)
    messages = detail["messages"]
    user_msgs = [m for m in messages if m["role"] == "user"]

    assert len(user_msgs) == 1, f"Expected exactly 1 user message, got: {user_msgs}"
    assert user_msgs[0]["content"] == "what is a heap?"


@pytest.mark.asyncio
async def test_get_detail_wrong_owner_raises(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        owner = await _make_user(s)
        other = await _make_user(s)
        await _seed_doc(s, owner.id)

    checkpointer = InMemorySaver()
    settings = _settings()
    model = FakeChatModel(
        responses=[
            _retrieve_call("x"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("answer"),
        ]
    )
    graph = build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=settings,
        checkpointer=checkpointer,
    )
    svc = ChatService(graph, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=owner.id,
        conversation_id=None,
        question="hello",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])

    with pytest.raises(ConversationNotFound):
        await svc.get_detail(convo_id, other.id)


# ---------------------------------------------------------------------------
# 7. Error frame path — graph error on new conversation cleans up orphan row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_error_frame_and_orphan_cleanup(_engine: AsyncEngine) -> None:
    """When the graph errors on a brand-new conversation the service must:
    1. Yield a 'meta' frame first (client still gets a conversation_id).
    2. Yield an 'error' frame (not crash silently).
    3. Delete the just-created conversation row (A-I1: no phantom in list_conversations).

    Forcing the error: FakeChatModel with an empty responses list raises IndexError
    on the very first LLM invocation — before any token is streamed.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        # Commit the user row; other tests rely on _seed_doc for this, but here
        # we deliberately skip seeding so the graph fails on the first LLM call.
        await s.commit()

    # Empty responses → FakeChatModel raises on the first call (forces graph error).
    model = FakeChatModel(responses=[])
    svc = _build_service(model, maker)

    gen = svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="this will error",
    )
    frames = await _collect_frames(gen)

    event_names = [e for e, _ in frames]

    # 1. meta frame must still be yielded (client learns the conversation_id).
    assert "meta" in event_names, f"Expected 'meta' frame before error, got: {event_names}"
    meta_data = next(d for e, d in frames if e == "meta")
    convo_id = uuid.UUID(meta_data["conversation_id"])

    # 2. error frame must be present with a 'detail' key.
    assert "error" in event_names, f"Expected 'error' frame, got: {event_names}"
    error_data = next(d for e, d in frames if e == "error")
    assert "detail" in error_data, f"'error' frame must carry 'detail', got: {error_data}"

    # 3. orphan row must be cleaned up (A-I1).
    convos = await svc.list_conversations(user.id)
    assert convos == [], (
        f"Orphan conversation row was NOT deleted after a no-output error; "
        f"found: {[str(c.id) for c in convos]}"
    )
    # Also confirm the specific row is gone.
    async with maker() as s:
        from app.db.repositories.conversation import ConversationRepository as _CR
        row = await _CR(s).get_for_user(convo_id, user.id)
    assert row is None, "The orphan conversation row still exists in the database."


# ---------------------------------------------------------------------------
# 6. delete_conversation — removes row, get_detail then raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_conversation(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    checkpointer = InMemorySaver()
    settings = _settings()
    model = FakeChatModel(
        responses=[
            _retrieve_call("heap"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure."),
        ]
    )
    graph = build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=settings,
        checkpointer=checkpointer,
    )
    svc = ChatService(graph, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])

    await svc.delete_conversation(convo_id, user.id)

    with pytest.raises(ConversationNotFound):
        await svc.get_detail(convo_id, user.id)
