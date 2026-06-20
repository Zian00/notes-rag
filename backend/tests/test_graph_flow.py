"""End-to-end flow tests for the compiled RAG graph.

Each test compiles the graph with FakeChatModel + InMemorySaver (no real LLM, no
network, no psycopg pool).  A seeded test DB is used only for tool tests that need real
chunk data; pure routing tests use an in-memory sessionmaker over an empty engine.

Scenarios covered (per Milestone C plan):
1. Happy path — retrieve → grade relevant → answer with context populated.
2. Corrective loop — grade False then True → exactly one rewrite, retry_count == 1.
3. Retry cap — always-False grades → at most max_grade_retries rewrites, then generate.
4. Whole-doc path — get_document_content tool call → tools → agent → generate, grade skipped.
5. Direct answer — AIMessage with no tool_calls → straight to generate.
6. Linear fallback — agentic_retrieval=False → retrieves with no agent tool decision.
7. Multi-turn — two invokes on same thread_id, second turn state includes first turn's messages.
"""

import uuid
from typing import Any

import pytest
from app.core.config import Settings
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.rag.graph import build_rag_graph
from app.rag.graph.nodes import Grade
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.fakes import FakeChatModel, FakeEmbeddingsProvider

DIM = 1536


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


async def _seed_user_and_doc(session: AsyncSession) -> tuple[Any, Any]:
    user = await UserRepository(session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(session).create(
        user_id=user.id,
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
                "user_id": user.id,
                "chunk_index": 0,
                "content": "heap is a tree-based structure",
                "embedding": _vec(0),
            }
        ]
    )
    await session.commit()
    return user, doc


def _build_graph(model: FakeChatModel, maker: async_sessionmaker, **setting_overrides: Any) -> Any:  # type: ignore[type-arg]
    s = _settings(**setting_overrides)
    return build_rag_graph(
        chat_model=model,
        embeddings=FakeEmbeddingsProvider(DIM),
        sessionmaker=maker,
        settings=s,
        checkpointer=InMemorySaver(),
    )


async def _run(graph: Any, user_id: uuid.UUID, question: str, thread_id: str) -> dict[str, Any]:
    """Invoke the graph and return the final state values."""
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
    inputs = {
        "messages": [HumanMessage(question)],
        "question": question,
        "retry_count": 0,
    }
    final_state = await graph.ainvoke(inputs, config)
    return final_state


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(_engine: AsyncEngine) -> None:
    """Retrieve → grade relevant → answer; context is populated in final state."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, doc = await _seed_user_and_doc(s)

    # Scripted responses in execution order:
    # 1. agent: calls retrieve_notes
    # 2. grade: relevant=True
    # 3. generate: final answer
    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "retrieve_notes", "args": {"query": "heap"}, "id": "tc1"}],
            ),
            Grade(relevant=True),
            AIMessage("A heap is a tree-based structure [1]."),
        ]
    )

    graph = _build_graph(model, maker)
    state = await _run(graph, user.id, "what is a heap?", f"t-{uuid.uuid4().hex}")

    # Final answer should be present in messages.
    ai_msgs = [m for m in state["messages"] if isinstance(m, AIMessage) and m.content]
    assert any("heap" in m.content.lower() for m in ai_msgs)
    # Context must be populated after retrieval.
    assert state.get("context")


# ---------------------------------------------------------------------------
# 2. Corrective loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrective_loop(_engine: AsyncEngine) -> None:
    """Grade False then True → exactly one rewrite, retry_count == 1 in final state."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed_user_and_doc(s)

    model = FakeChatModel(
        responses=[
            # First agent call: retrieve
            AIMessage(
                content="",
                tool_calls=[{"name": "retrieve_notes", "args": {"query": "heap"}, "id": "tc1"}],
            ),
            # First grade: not relevant
            Grade(relevant=False),
            # Rewrite: returns a new query
            AIMessage("heap data structure definition"),
            # Second agent call: retrieve again with rewritten query
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_notes",
                        "args": {"query": "heap data structure"},
                        "id": "tc2",
                    }
                ],
            ),
            # Second grade: relevant
            Grade(relevant=True),
            # Generate: final answer
            AIMessage("A heap is a tree-based data structure [1]."),
        ]
    )

    graph = _build_graph(model, maker)
    state = await _run(graph, user.id, "what is a heap?", f"t-{uuid.uuid4().hex}")

    assert state.get("retry_count") == 1
    ai_msgs = [
        m for m in state["messages"] if isinstance(m, AIMessage) and m.content and not m.tool_calls
    ]
    assert any("heap" in m.content.lower() for m in ai_msgs)


# ---------------------------------------------------------------------------
# 3. Retry cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_cap(_engine: AsyncEngine) -> None:
    """Always-False grades → at most max_grade_retries rewrites, then generate anyway."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed_user_and_doc(s)

    # max_grade_retries=2: agent → retrieve → grade(F) → rewrite → agent → retrieve →
    # grade(F) → rewrite → agent → retrieve → grade(F) → generate (exhausted)
    max_retries = 2
    responses: list[Any] = []
    for i in range(max_retries + 1):
        responses.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "retrieve_notes", "args": {"query": f"q{i}"}, "id": f"tc{i}"}],
            )
        )
        responses.append(Grade(relevant=False))  # always irrelevant
        if i < max_retries:
            responses.append(AIMessage(f"rewritten query {i}"))  # rewrite response

    # After exhausting retries, generate is called.
    responses.append(AIMessage("I couldn't find this in your notes."))

    model = FakeChatModel(responses=responses)
    graph = _build_graph(model, maker, max_grade_retries=max_retries)
    state = await _run(graph, user.id, "mystery topic", f"t-{uuid.uuid4().hex}")

    assert state.get("retry_count") == max_retries
    # Final message should be the generate answer (not a tool call).
    last_ai = [m for m in state["messages"] if isinstance(m, AIMessage) and not m.tool_calls]
    assert last_ai  # some non-tool AI message was produced
    assert "couldn't find" in last_ai[-1].content.lower()


# ---------------------------------------------------------------------------
# 4. Whole-doc path (get_document_content → agent → generate, grade skipped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whole_doc_path(_engine: AsyncEngine) -> None:
    """get_document_content routes back to agent (not grade); then agent generates."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, doc = await _seed_user_and_doc(s)

    model = FakeChatModel(
        responses=[
            # Agent step 1: call list_documents to find the doc id.
            AIMessage(
                content="",
                tool_calls=[{"name": "list_documents", "args": {}, "id": "tc1"}],
            ),
            # Agent step 2: after seeing the list, call get_document_content.
            # (list_documents routes back to agent, not grade)
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_document_content",
                        "args": {"document_id": str(doc.id)},
                        "id": "tc2",
                    }
                ],
            ),
            # Agent step 3: after receiving the full document, answer directly (no tool call).
            # (get_document_content routes back to agent, not grade)
            AIMessage("This document covers heap data structures."),
            # Generate step: produce the final answer.
            AIMessage("This document covers heap data structures."),
        ]
    )

    graph = _build_graph(model, maker)
    state = await _run(graph, user.id, "summarise my lecture notes", f"t-{uuid.uuid4().hex}")

    # Final answer should be the summary.
    ai_msgs = [
        m for m in state["messages"] if isinstance(m, AIMessage) and m.content and not m.tool_calls
    ]
    assert any("heap" in m.content.lower() or "document" in m.content.lower() for m in ai_msgs)


# ---------------------------------------------------------------------------
# 5. Direct answer (no tool calls → straight to generate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_answer(_engine: AsyncEngine) -> None:
    """If the agent produces no tool_calls, the graph goes straight to generate."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed_user_and_doc(s)

    model = FakeChatModel(
        responses=[
            # Agent: no tool calls needed.
            AIMessage("Hello! How can I help you today?"),
            # Generate is called next.
            AIMessage("Hello! How can I help you today?"),
        ]
    )

    graph = _build_graph(model, maker)
    state = await _run(graph, user.id, "hello", f"t-{uuid.uuid4().hex}")

    ai_msgs = [
        m for m in state["messages"] if isinstance(m, AIMessage) and m.content and not m.tool_calls
    ]
    assert any("hello" in m.content.lower() for m in ai_msgs)


# ---------------------------------------------------------------------------
# 6. Linear fallback (agentic_retrieval=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_linear_fallback(_engine: AsyncEngine) -> None:
    """agentic_retrieval=False: retrieves immediately without an agent tool decision."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed_user_and_doc(s)

    # Linear path: force_retrieve → tools → grade → generate (no agent model involved in tool call).
    model = FakeChatModel(
        responses=[
            Grade(relevant=True),  # grade node
            AIMessage("Heap is a tree-based structure."),  # generate node
        ]
    )

    graph = _build_graph(model, maker, agentic_retrieval=False)
    state = await _run(graph, user.id, "heap", f"t-{uuid.uuid4().hex}")

    ai_msgs = [
        m for m in state["messages"] if isinstance(m, AIMessage) and m.content and not m.tool_calls
    ]
    assert ai_msgs
    assert "heap" in ai_msgs[-1].content.lower()


# ---------------------------------------------------------------------------
# 7. Multi-turn — second invoke on same thread has first turn's messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_turn(_engine: AsyncEngine) -> None:
    """InMemorySaver persists state across turns on the same thread_id."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed_user_and_doc(s)

    thread_id = f"t-{uuid.uuid4().hex}"

    # Both turns use the SAME graph (same InMemorySaver instance).
    # All scripted responses queued up front for turn 1 and turn 2 in order.
    model = FakeChatModel(
        responses=[
            # Turn 1: agent → no tool call → generate
            AIMessage("Hello, I'm here to help."),  # agent (direct answer, no tools)
            AIMessage("Hello, I'm here to help."),  # generate
            # Turn 2: agent → no tool call → generate
            AIMessage("Yes, as I recall, you greeted me."),  # agent
            AIMessage("Yes, as I recall, you greeted me."),  # generate
        ]
    )
    graph = _build_graph(model, maker)
    config = {"configurable": {"thread_id": thread_id, "user_id": user.id}}

    # Turn 1
    await graph.ainvoke(
        {"messages": [HumanMessage("hi")], "question": "hi", "retry_count": 0},
        config,
    )

    # Turn 2 — same graph, same thread_id: checkpointer resumes from turn 1 state.
    state2 = await graph.ainvoke(
        {
            "messages": [HumanMessage("what did I say?")],
            "question": "what did I say?",
            "retry_count": 0,
        },
        config,
    )

    # Second turn's accumulated messages should include messages from the first turn.
    all_msgs = state2["messages"]
    human_msgs = [m for m in all_msgs if isinstance(m, HumanMessage)]
    assert len(human_msgs) >= 2, f"Expected >= 2 HumanMessages across turns, got {len(human_msgs)}"
    contents = [m.content for m in human_msgs]
    assert "hi" in contents
    assert "what did I say?" in contents
