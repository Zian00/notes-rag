"""Tests for app/rag/graph/nodes.py — individual node callables + routers.

Nodes are called directly (no full graph compile), passing in a hand-crafted state dict
and a config carrying thread_id + user_id.  A FakeChatModel supplies scripted responses
so behaviour is deterministic without any real LLM or DB.
"""

import uuid
from typing import Any

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.rag.graph.nodes import (
    Grade,
    make_nodes,
    make_route_after_grade,
    route_after_agent,
    route_after_tools,
)
from app.rag.graph.tools import build_tools
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.conftest import hash_content
from tests.fakes import FakeChatModel, FakeEmbeddingsProvider

DIM = 1536
_CONFIG: dict[str, Any] = {"configurable": {"thread_id": "t1", "user_id": uuid.uuid4()}}


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot % DIM] = 1.0
    return v


async def _seed(session: AsyncSession) -> tuple[Any, Any]:
    """Create a user + document + 1 chunk; return (user, doc)."""
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
                "content": "hello world",
                "content_hash": hash_content("hello world"),
                "embedding": _vec(0),
            }
        ]
    )
    await session.commit()
    return user, doc


# ---------------------------------------------------------------------------
# condense node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_condense_skips_on_first_message(_engine: AsyncEngine) -> None:
    """No prior turn to resolve against → condense makes no LLM call and returns {}."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(responses=[])  # would raise IndexError if called
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("what is a heap?")],
        "question": "what is a heap?",
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["condense"](state, _CONFIG)
    assert result == {}


@pytest.mark.asyncio
async def test_condense_resolves_followup_using_history(_engine: AsyncEngine) -> None:
    """A follow-up question is rewritten into a standalone one using prior turns."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(responses=[AIMessage("what about a min-heap?")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [
            HumanMessage("what is a heap?"),
            AIMessage("A heap is a tree-based structure."),
            HumanMessage("what about a min one?"),
        ],
        "question": "what about a min one?",
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["condense"](state, _CONFIG)
    assert result == {"question": "what about a min-heap?"}


@pytest.mark.asyncio
async def test_condense_passes_through_already_standalone_question(
    _engine: AsyncEngine,
) -> None:
    """An already-standalone question is returned unchanged (per CONDENSE_SYSTEM's
    instruction not to rewrite what doesn't need it)."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    # The fake model echoes the question back verbatim, simulating a real LLM
    # correctly recognising nothing needs resolving.
    model = FakeChatModel(responses=[AIMessage("what is a binary search tree?")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [
            HumanMessage("what is a heap?"),
            AIMessage("A heap is a tree-based structure."),
            HumanMessage("what is a binary search tree?"),
        ],
        "question": "what is a binary search tree?",
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["condense"](state, _CONFIG)
    assert result == {"question": "what is a binary search tree?"}


# ---------------------------------------------------------------------------
# agent node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_emits_tool_call(_engine: AsyncEngine) -> None:
    """agent node returns a message with tool_calls when the model requests a tool."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed(s)

    tool_msg = AIMessage(
        content="",
        tool_calls=[{"name": "retrieve_notes", "args": {"query": "test"}, "id": "tc1"}],
    )
    model = FakeChatModel(responses=[tool_msg])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("what is a heap?")],
        "question": "what is a heap?",
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["agent"](state, _CONFIG)

    assert "messages" in result
    last = result["messages"][-1]
    assert hasattr(last, "tool_calls")
    assert last.tool_calls[0]["name"] == "retrieve_notes"


@pytest.mark.asyncio
async def test_agent_direct_answer(_engine: AsyncEngine) -> None:
    """agent node returns a plain AIMessage (no tool calls) for a direct answer."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed(s)

    model = FakeChatModel(responses=[AIMessage("Hello, how can I help?")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("hi")],
        "question": "hi",
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["agent"](state, _CONFIG)

    last = result["messages"][-1]
    assert not getattr(last, "tool_calls", None)
    assert "Hello" in last.content


@pytest.mark.asyncio
async def test_agent_surfaces_question_ephemerally(_engine: AsyncEngine) -> None:
    """agent's LLM call sees the current (condensed/rewritten) question, but the
    ephemeral note is never persisted back into the state patch's messages."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    captured_msgs: list[Any] = []

    class CapturingFake(FakeChatModel):
        async def _agenerate(self, messages: Any, **kw: Any) -> Any:  # type: ignore[override]
            captured_msgs.extend(messages)
            return await super()._agenerate(messages, **kw)

    model = CapturingFake(responses=[AIMessage("min-heaps keep the smallest element on top")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("what about a min one?")],
        "question": "what about a min-heap?",  # resolved by condense/rewrite
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["agent"](state, _CONFIG)

    full_text = " ".join(str(getattr(m, "content", "")) for m in captured_msgs)
    assert "what about a min-heap?" in full_text
    # Only the model's own response is persisted — no ephemeral note leaks into state.
    assert len(result["messages"]) == 1
    assert all(not isinstance(m, HumanMessage) for m in result["messages"])


# ---------------------------------------------------------------------------
# tools_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_node_retrieve_populates_context(_engine: AsyncEngine) -> None:
    """tools_node populates context when retrieve_notes is called."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed(s)

    model = FakeChatModel(responses=[])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    # State where the last message is an AIMessage requesting retrieve_notes.
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[{"name": "retrieve_notes", "args": {"query": "hello"}, "id": "tc1"}],
    )
    config: dict[str, Any] = {"configurable": {"thread_id": "t1", "user_id": user.id}}
    state: dict[str, Any] = {
        "messages": [HumanMessage("q?"), tool_call_msg],
        "question": "q?",
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["tools"](state, config)

    assert "messages" in result
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].name == "retrieve_notes"  # name must be set for router
    assert "context" in result  # context should be populated


@pytest.mark.asyncio
async def test_tools_node_list_docs_no_context(_engine: AsyncEngine) -> None:
    """tools_node does NOT update context when list_documents is called."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        user, _ = await _seed(s)

    model = FakeChatModel(responses=[])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    tool_call_msg = AIMessage(
        content="",
        tool_calls=[{"name": "list_documents", "args": {}, "id": "tc2"}],
    )
    prior_context = [{"chunk_id": "abc", "content": "prior"}]
    config: dict[str, Any] = {"configurable": {"thread_id": "t1", "user_id": user.id}}
    state: dict[str, Any] = {
        "messages": [tool_call_msg],
        "question": "q?",
        "context": prior_context,
        "retry_count": 0,
    }
    result = await nodes["tools"](state, config)

    # context should remain unchanged (list_documents is not a retrieval tool for grading)
    assert result.get("context") == prior_context


# ---------------------------------------------------------------------------
# grade node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grade_sets_relevant_true(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(
        responses=[Grade(relevant=True, reason="context answers the question")]
    )
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("q?")],
        "question": "q?",
        "context": [
            {
                "content": "relevant answer", "title": "L1",
                "filename": "l1.pdf", "page_number": None, "section": None,
            }
        ],
        "retry_count": 0,
    }
    result = await nodes["grade"](state, _CONFIG)
    assert result["relevant"] is True


@pytest.mark.asyncio
async def test_grade_sets_relevant_false(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(
        responses=[Grade(relevant=False, reason="context does not answer the question")]
    )
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("q?")],
        "question": "q?",
        "context": [
            {
                "content": "unrelated text", "title": "L2",
                "filename": "l2.pdf", "page_number": None, "section": None,
            }
        ],
        "retry_count": 0,
    }
    result = await nodes["grade"](state, _CONFIG)
    assert result["relevant"] is False


@pytest.mark.asyncio
async def test_grade_reason_flows_into_state(_engine: AsyncEngine) -> None:
    """grade's structured reason is surfaced as grade_reason in the state patch."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(
        responses=[Grade(relevant=False, reason="context is about stacks, not heaps")]
    )
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("q?")],
        "question": "q?",
        "context": [
            {
                "content": "a stack is LIFO", "title": "L2",
                "filename": "l2.pdf", "page_number": None, "section": None,
            }
        ],
        "retry_count": 0,
    }
    result = await nodes["grade"](state, _CONFIG)
    assert result["grade_reason"] == "context is about stacks, not heaps"


# ---------------------------------------------------------------------------
# rewrite node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_increments_retry_count(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(responses=[AIMessage("better search query")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("vague question")],
        "question": "vague question",
        "context": [],
        "retry_count": 0,
    }
    result = await nodes["rewrite"](state, _CONFIG)

    assert result["retry_count"] == 1
    assert result["question"] == "better search query"
    # Regression test: rewrite must NOT persist a synthetic message into the
    # conversation history (previously it appended HumanMessage(new_q) — the bug
    # ChatService.get_detail would echo back as a fake "user" turn).
    assert "messages" not in result


@pytest.mark.asyncio
async def test_rewrite_prompt_includes_reason_and_context(_engine: AsyncEngine) -> None:
    """rewrite's LLM call is informed by grade's reason and the failed context, not a
    blind rephrase of the original question."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    captured_msgs: list[Any] = []

    class CapturingFake(FakeChatModel):
        async def _agenerate(self, messages: Any, **kw: Any) -> Any:  # type: ignore[override]
            captured_msgs.extend(messages)
            return await super()._agenerate(messages, **kw)

    model = CapturingFake(responses=[AIMessage("back-propagation training")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("how does backprop work")],
        "question": "how does backprop work",
        "context": [
            {
                "content": "back-propagation computes gradients layer by layer",
                "title": "L3", "filename": "l3.pdf", "page_number": None, "section": None,
            }
        ],
        "grade_reason": "vocabulary mismatch: notes use 'back-propagation', not 'backprop'",
        "retry_count": 0,
    }
    await nodes["rewrite"](state, _CONFIG)

    full_text = " ".join(str(getattr(m, "content", "")) for m in captured_msgs)
    assert "vocabulary mismatch" in full_text
    assert "back-propagation computes gradients" in full_text


@pytest.mark.asyncio
async def test_rewrite_increments_from_nonzero(_engine: AsyncEngine) -> None:
    """rewrite adds 1 to whatever retry_count is in state."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(responses=[AIMessage("refined query")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("q")],
        "question": "q",
        "context": [],
        "retry_count": 1,
    }
    result = await nodes["rewrite"](state, _CONFIG)
    assert result["retry_count"] == 2


# ---------------------------------------------------------------------------
# generate node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_ai_message(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(responses=[AIMessage("The answer is [1].")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("q?")],
        "question": "q?",
        "context": [
            {
                "content": "answer text", "title": "L1",
                "filename": "l1.pdf", "page_number": 1, "section": None,
            }
        ],
        "retry_count": 0,
    }
    result = await nodes["generate"](state, _CONFIG)

    assert "messages" in result
    ai_msg = result["messages"][-1]
    assert isinstance(ai_msg, AIMessage)
    assert "answer" in ai_msg.content.lower()


@pytest.mark.asyncio
async def test_generate_with_empty_context_prompted_with_no_results(_engine: AsyncEngine) -> None:
    """With empty context, generate is called with 'NO RESULTS.' in the prompt."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    captured_msgs: list[Any] = []

    class CapturingFake(FakeChatModel):
        async def _agenerate(self, messages: Any, **kw: Any) -> Any:  # type: ignore[override]
            captured_msgs.extend(messages)
            return await super()._agenerate(messages, **kw)

    model = CapturingFake(responses=[AIMessage("I couldn't find this in your notes.")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("q?")],
        "question": "q?",
        "context": [],
        "retry_count": 0,
    }
    await nodes["generate"](state, _CONFIG)

    full_text = " ".join(str(getattr(m, "content", "")) for m in captured_msgs)
    assert "NO RESULTS" in full_text


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


def test_route_after_agent_to_tools() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "retrieve_notes", "args": {"query": "q"}, "id": "tc1"}],
    )
    state: dict[str, Any] = {"messages": [msg]}
    assert route_after_agent(state) == "tools"  # type: ignore[arg-type]


def test_route_after_agent_to_generate() -> None:
    state: dict[str, Any] = {"messages": [AIMessage("direct answer")]}
    assert route_after_agent(state) == "generate"  # type: ignore[arg-type]


def test_route_after_tools_grade_for_retrieve() -> None:
    tm = ToolMessage(content="...", tool_call_id="tc1", name="retrieve_notes")
    state: dict[str, Any] = {"messages": [tm]}
    assert route_after_tools(state) == "grade"  # type: ignore[arg-type]


def test_route_after_tools_agent_for_list_docs() -> None:
    tm = ToolMessage(content="...", tool_call_id="tc2", name="list_documents")
    state: dict[str, Any] = {"messages": [tm]}
    assert route_after_tools(state) == "agent"  # type: ignore[arg-type]


def test_route_after_tools_agent_for_get_document_content() -> None:
    """get_document_content is a deliberate fetch → routes back to agent (not grade)."""
    tm = ToolMessage(content="...", tool_call_id="tc3", name="get_document_content")
    state: dict[str, Any] = {"messages": [tm]}
    assert route_after_tools(state) == "agent"  # type: ignore[arg-type]


def test_route_after_grade_relevant() -> None:
    router = make_route_after_grade(max_retries=2)
    state: dict[str, Any] = {"messages": [], "relevant": True, "retry_count": 0}
    assert router(state) == "generate"  # type: ignore[arg-type]


def test_route_after_grade_rewrite_when_retries_left() -> None:
    router = make_route_after_grade(max_retries=2)
    state: dict[str, Any] = {"messages": [], "relevant": False, "retry_count": 0}
    assert router(state) == "rewrite"  # type: ignore[arg-type]


def test_route_after_grade_generate_when_retries_exhausted() -> None:
    router = make_route_after_grade(max_retries=2)
    state: dict[str, Any] = {"messages": [], "relevant": False, "retry_count": 2}
    assert router(state) == "generate"  # type: ignore[arg-type]
