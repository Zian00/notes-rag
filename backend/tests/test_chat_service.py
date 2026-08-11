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
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.conversation import ConversationRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.models.group import Group
from app.rag.graph import build_rag_graph
from app.rag.graph.nodes import CondensedQuestion, Grade, Triage
from app.services.chat import ChatService, ConversationNotFound
from langchain_core.messages import AIMessage, HumanMessage
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


def _build_service(
    model: FakeChatModel,
    maker: async_sessionmaker,  # type: ignore[type-arg]
    checkpointer: Any = None,
) -> ChatService:
    """Compile the graph with fakes and wrap it in a ChatService.

    Pass a shared `checkpointer` for multi-turn tests: turn 2 only resumes turn 1's
    thread if both services were built against the same saver. Defaults to a fresh
    InMemorySaver, which is what single-turn tests want.
    """
    graph = build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=_settings(),
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
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
            CondensedQuestion(is_follow_up=True, standalone_question="what about a min-heap?"),
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
# 3b. A no-tool turn must not inherit the previous turn's context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_direct_answer_turn_does_not_inherit_previous_context(
    _engine: AsyncEngine,
) -> None:
    """A turn where the agent answers directly must report no sources.

    The agent answers greetings itself, so the tools node never runs on that turn.
    Since `context` is written *only* by the tools node, the checkpointer would
    otherwise replay the previous turn's chunks into it — which both made generate
    refuse the greeting and attributed the earlier answer's sources to it.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    checkpointer = InMemorySaver()  # shared so turn 2 resumes the same thread

    def _svc_for(model: FakeChatModel) -> ChatService:
        return _build_service(model, maker, checkpointer)

    # Turn 1 populates context. Condense is skipped — there is no prior turn yet.
    turn1 = FakeChatModel(
        responses=[
            _retrieve_call("heap", "t1"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure [1]."),
        ]
    )
    frames1 = await _collect_frames(_svc_for(turn1).stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames1 if e == "meta")["conversation_id"])
    # Guards the test itself: if turn 1 retrieved nothing there is no stale context
    # to leak, so turn 2's assertion below would pass for the wrong reason.
    assert next(d for e, d in frames1 if e == "citations"), "turn 1 should have sources"

    # Turn 2 is a greeting: condense → agent replies directly → generate. No tools node.
    turn2 = FakeChatModel(
        responses=[
            CondensedQuestion(is_follow_up=False),  # a greeting is not a follow-up
            AIMessage("Hello! How can I help with your notes?"),  # agent, no tool_calls
            AIMessage("Hello! How can I help with your notes?"),  # generate
        ]
    )
    frames2 = await _collect_frames(_svc_for(turn2).stream_answer(
        user_id=user.id,
        conversation_id=convo_id,
        question="hi",
    ))

    assert next(d for e, d in frames2 if e == "citations") == []


# ---------------------------------------------------------------------------
# 3c. A greeting is answered by the agent, not refused by generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_greeting_returns_the_agents_reply(_engine: AsyncEngine) -> None:
    """"hi" must come back as a greeting, and the conversation must survive.

    generate refuses whenever it has no context, so routing a greeting through it
    produced "I couldn't find this in your notes." in reply to "hi". The agent's own
    reply now ends the turn — and it must still reach the client as a token frame,
    or `streamed_any` stays False and the brand-new conversation row is deleted as
    an orphan, making the chat vanish from the sidebar.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    # A single response: the agent answers directly. If generate were still reached
    # it would need a second response and raise IndexError — so this scripting also
    # proves the node is skipped entirely.
    model = FakeChatModel(responses=[AIMessage("Hello! How can I help with your notes?")])
    svc = _build_service(model, maker)

    frames = await _collect_frames(
        svc.stream_answer(user_id=user.id, conversation_id=None, question="hi")
    )

    tokens = "".join(d["delta"] for e, d in frames if e == "token")
    assert "Hello!" in tokens
    assert "couldn't find this in your notes" not in tokens
    assert next(d for e, d in frames if e == "citations") == []

    # The conversation row must survive (streamed_any was set → no orphan cleanup).
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])
    async with maker() as s:
        from app.db.repositories.conversation import ConversationRepository
        assert await ConversationRepository(s).get_for_user(convo_id, user.id) is not None

    # And it must be replayable — get_detail keys off the same marker the agent set.
    detail = await svc.get_detail(convo_id, user.id)
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert "Hello!" in assistant[0]["content"]


# ---------------------------------------------------------------------------
# 3d. Greeting AFTER a successful grounded answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_greeting_after_a_grounded_answer(_engine: AsyncEngine) -> None:
    """Q → grounded answer → "hi" must greet, and must not disturb the earlier turn.

    This is the full-stack version of the reported bug. Two separate faults met here:
    the greeting inherited the previous turn's `context` (so it was refused AND shown
    the earlier answer's sources), and it was routed through generate at all. Both the
    live stream and the replayed history are asserted, since the original symptom was
    visible in one and not the other.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    checkpointer = InMemorySaver()  # shared so turn 2 resumes the same thread

    def _svc_for(model: FakeChatModel) -> ChatService:
        return _build_service(model, maker, checkpointer)

    # Turn 1: a real question. condense is skipped (no prior turn).
    turn1 = FakeChatModel(
        responses=[
            _retrieve_call("heap", "t1"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure [1]."),
        ]
    )
    frames1 = await _collect_frames(_svc_for(turn1).stream_answer(
        user_id=user.id, conversation_id=None, question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames1 if e == "meta")["conversation_id"])
    assert next(d for e, d in frames1 if e == "citations"), "turn 1 must have sources"

    # Turn 2: only two model calls are scripted — condense, then the agent's reply.
    # If generate were still reached it would need a third and raise IndexError, so
    # this scripting is itself the assertion that the turn ends at the agent.
    turn2 = FakeChatModel(
        responses=[
            CondensedQuestion(is_follow_up=False),  # a greeting is not a follow-up
            AIMessage("Hello! How can I help with your notes?"),  # agent, no tool call
        ]
    )
    frames2 = await _collect_frames(_svc_for(turn2).stream_answer(
        user_id=user.id, conversation_id=convo_id, question="hi",
    ))

    tokens = "".join(d["delta"] for e, d in frames2 if e == "token")
    assert "Hello!" in tokens
    assert "couldn't find this in your notes" not in tokens
    assert next(d for e, d in frames2 if e == "citations") == []

    # Replayed history: both answers present, sources attached to the right one.
    detail = await _svc_for(turn2).get_detail(convo_id, user.id)
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert len(assistant) == 2, f"expected 2 answers, got {[m['content'] for m in assistant]}"
    assert assistant[0]["citations"], "turn 1's sources must survive the greeting"
    assert not assistant[1]["citations"], "a greeting must claim no sources"


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

    # triage → force_retrieve → grade(False) → rewrite → force_retrieve → grade(True)
    # → generate. rewrite loops back to force_retrieve, not triage, so triage runs once.
    model = FakeChatModel(
        responses=[
            Triage(needs_notes=True),
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
async def test_get_detail_excludes_agent_node_answer(_engine: AsyncEngine) -> None:
    """Replayed history must show only the grounded `generate` answer.

    Regression test: the agent node can answer directly from the model's own
    knowledge (no tool call), and that AIMessage lands in checkpointer state
    alongside generate's. Live SSE only streams generate's tokens, so such an
    answer was invisible live but reappeared as a second assistant bubble on
    reload — presenting ungrounded text as though it were sourced from notes.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        doc = await _seed_doc(s, user.id)

    # Reading a document routes back to the agent, which then writes a draft answer
    # with no tool_calls. Because context is now non-empty that draft is NOT the end
    # of the turn — generate still runs and owns the answer, so the draft must stay
    # out of replayed history.
    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "get_document_content",
                    "args": {"document_id": str(doc.id)},
                    "id": "tc1",
                }],
            ),
            AIMessage("Ungrounded draft the agent wrote."),
            AIMessage("A heap is a tree-based structure [1]."),
        ]
    )
    svc = _build_service(model, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="summarise my notes",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])

    detail = await svc.get_detail(convo_id, user.id)
    assistant_contents = [m["content"] for m in detail["messages"] if m["role"] == "assistant"]

    assert assistant_contents == ["A heap is a tree-based structure [1]."], (
        f"Expected only generate's grounded answer, got: {assistant_contents}"
    )


@pytest.mark.asyncio
async def test_agent_answering_a_topic_without_searching_is_no_longer_blocked(
    _engine: AsyncEngine,
) -> None:
    """Documents a KNOWN GAP, so it fails loudly if the trade-off is revisited.

    Ending the turn on a conversational reply means the graph can no longer tell a
    greeting from a subject question the agent chose not to search for — both are
    "no tool call, nothing citable, never searched". Previously generate caught the
    latter and refused. That guarantee now rests solely on AGENT_SYSTEM, which
    instructs the model to always call a tool for subject-matter questions.

    If this ever starts happening in practice, the fix belongs in the agent prompt or
    in an explicit conversational/subject signal — not in weakening the routing rule,
    which is what makes greetings work at all.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    # A disobedient agent: answers a subject question outright, never searching.
    model = FakeChatModel(
        responses=[AIMessage("A weak entity cannot be identified by its own attributes.")]
    )
    svc = _build_service(model, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="explain weak entity",
    ))

    tokens = "".join(d["delta"] for e, d in frames if e == "token")
    assert "weak entity cannot be identified" in tokens  # reaches the user, ungrounded
    assert next(d for e, d in frames if e == "citations") == []  # but claims no sources


@pytest.mark.asyncio
async def test_get_detail_returns_persisted_citations(_engine: AsyncEngine) -> None:
    """Citations must survive reopening a conversation.

    They're delivered live over SSE, but that frame is transient — the answer
    message carries its own copy so replayed history can show sources too.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        await _seed_doc(s, user.id)

    model = FakeChatModel(
        responses=[
            _retrieve_call("heap"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("A heap is a tree-based structure [1]."),
        ]
    )
    svc = _build_service(model, maker)

    frames = await _collect_frames(svc.stream_answer(
        user_id=user.id,
        conversation_id=None,
        question="what is a heap?",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames if e == "meta")["conversation_id"])
    live_citations = next(d for e, d in frames if e == "citations")
    assert live_citations, "precondition: this turn should have streamed citations live"

    detail = await svc.get_detail(convo_id, user.id)
    assistant = next(m for m in detail["messages"] if m["role"] == "assistant")

    # Replayed history must carry the same sources the live stream sent.
    assert assistant["citations"] == live_citations


@pytest.mark.asyncio
async def test_get_detail_legacy_thread_without_markers_still_shows_answers(
    _engine: AsyncEngine,
) -> None:
    """Threads checkpointed before FINAL_ANSWER_KEY existed carry no markers; those
    must fall back to showing every AIMessage rather than replaying with no replies."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        from app.db.repositories.conversation import ConversationRepository

        convo = await ConversationRepository(s).create(user_id=user.id, title="legacy")
        await s.commit()
        convo_id = convo.id

    class _StubState:
        def __init__(self, values: dict[str, Any]) -> None:
            self.values = values

    class _StubGraph:
        """Stands in for the compiled graph: get_detail only ever calls aget_state."""

        async def aget_state(self, _config: dict[str, Any]) -> _StubState:
            return _StubState(
                {"messages": [HumanMessage("old question"), AIMessage("old answer")]}
            )

    svc = ChatService(_StubGraph(), maker)
    detail = await svc.get_detail(convo_id, user.id)

    assert [m["content"] for m in detail["messages"]] == ["old question", "old answer"]


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


async def _seed_doc_in_group(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    group_name: str,
    content: str,
    slot: int,
) -> tuple[Any, Any]:
    """Create a group + one document (in it) + one chunk; return (group, doc)."""
    group = Group(user_id=user_id, name=group_name)
    session.add(group)
    await session.flush()  # populate group.id
    doc = await DocumentRepository(session).create(
        user_id=user_id,
        filename=f"{group_name}.pdf",
        title=group_name,
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path=f"/tmp/{group_name}.pdf",
        file_size=1,
        chunk_count=0,
        embedding_model="test",
        embedding_dimension=DIM,
        group_id=group.id,
    )
    await ChunkRepository(session).add_many(
        [{
            "document_id": doc.id,
            "user_id": user_id,
            "chunk_index": 0,
            "content": content,
            "content_hash": hash_content(content),
            "embedding": _vec(slot),
        }]
    )
    await session.commit()
    return group, doc


@pytest.mark.asyncio
async def test_stream_group_scope_is_server_enforced(_engine: AsyncEngine) -> None:
    """Group scope is resolved server-side, not from the client.

    A new conversation created in group A retrieves ONLY group A's documents; and
    when a later turn on that same conversation passes a *different* group_id, the
    client value is ignored — the stored conversation.group_id (A) still wins.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user = await _make_user(s)
        group_a, doc_a = await _seed_doc_in_group(
            s, user.id, group_name="A", content="alpha lives in group A", slot=0
        )
        group_b, doc_b = await _seed_doc_in_group(
            s, user.id, group_name="B", content="beta lives in group B", slot=1
        )

    checkpointer = InMemorySaver()  # shared so turn 2 resumes the same thread

    # Turn 1: new conversation scoped to group A.
    turn1 = FakeChatModel(
        responses=[
            _retrieve_call("notes", "t1"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("answer [1]."),
        ]
    )
    frames1 = await _collect_frames(_build_service(turn1, maker, checkpointer).stream_answer(
        user_id=user.id, conversation_id=None, group_id=group_a.id, question="q1",
    ))
    convo_id = uuid.UUID(next(d for e, d in frames1 if e == "meta")["conversation_id"])
    doc_ids_1 = {c["document_id"] for c in next(d for e, d in frames1 if e == "citations")}
    assert str(doc_a.id) in doc_ids_1
    assert str(doc_b.id) not in doc_ids_1  # group B excluded (strict)

    # The chosen group must be persisted on the row.
    async with maker() as s:
        row = await ConversationRepository(s).get_for_user(convo_id, user.id)
    assert row is not None and row.group_id == group_a.id

    # Turn 2: client tries to switch scope to group B — must be ignored.
    turn2 = FakeChatModel(
        responses=[
            CondensedQuestion(is_follow_up=False),
            _retrieve_call("notes", "t2"),
            Grade(relevant=True, reason="context answers the question"),
            AIMessage("answer [1]."),
        ]
    )
    frames2 = await _collect_frames(_build_service(turn2, maker, checkpointer).stream_answer(
        user_id=user.id, conversation_id=convo_id, group_id=group_b.id, question="q2",
    ))
    doc_ids_2 = {c["document_id"] for c in next(d for e, d in frames2 if e == "citations")}
    assert str(doc_a.id) in doc_ids_2  # still group A
    assert str(doc_b.id) not in doc_ids_2  # client-supplied group B ignored


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
