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
    CondensedQuestion,
    Grade,
    _message_text,
    make_nodes,
    make_route_after_grade,
    route_after_agent,
    route_after_tools,
)
from app.rag.graph.state import CITATIONS_KEY, FINAL_ANSWER_KEY
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


def _condense_state(question: str) -> dict[str, Any]:
    """State with one completed prior turn, so condense runs rather than skipping."""
    # Marked, as generate marks every real answer — an unmarked AIMessage is an
    # ungrounded draft and _recent strips it, so it would never reach condense.
    answer = AIMessage("A heap is a tree-based structure.")
    answer.additional_kwargs[FINAL_ANSWER_KEY] = True
    return {
        "messages": [
            HumanMessage("what is a heap?"),
            answer,
            HumanMessage(question),
        ],
        "question": question,
        "context": [],
        "retry_count": 0,
    }


@pytest.mark.asyncio
async def test_condense_resolves_followup_using_history(_engine: AsyncEngine) -> None:
    """A genuine follow-up is rewritten into a standalone question."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(
        responses=[
            CondensedQuestion(is_follow_up=True, standalone_question="what about a min-heap?")
        ]
    )
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    result = await nodes["condense"](_condense_state("what about a min one?"), _CONFIG)
    assert result == {"question": "what about a min-heap?"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "hi",
        "what notes do i have?",
        "summarise Topic3 BCNF(1)",
        "what is a binary search tree?",
    ],
)
async def test_condense_leaves_standalone_messages_untouched(
    _engine: AsyncEngine, question: str
) -> None:
    """A message that stands alone must reach the agent exactly as typed.

    These four are the real cases observed in production. Condense answered three of
    them instead of rewriting — "what notes do i have?" became "I'm unable to access
    your personal notes...", which was then stored as the turn's question and handed
    to the agent, which apologised for a misunderstanding. Returning {} leaves
    `question` as the user typed it, so a wrong rewrite cannot reach the agent at all.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    # Even when the model fills in a rewrite, is_follow_up=False must discard it.
    model = FakeChatModel(
        responses=[
            CondensedQuestion(
                is_follow_up=False,
                standalone_question="I'm unable to access your personal notes.",
            )
        ]
    )
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    result = await nodes["condense"](_condense_state(question), _CONFIG)
    assert result == {}


@pytest.mark.asyncio
async def test_condense_falls_back_when_the_rewrite_is_empty(_engine: AsyncEngine) -> None:
    """An empty rewrite must not become the question — the retriever would search ""."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(
        responses=[CondensedQuestion(is_follow_up=True, standalone_question="   ")]
    )
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    result = await nodes["condense"](_condense_state("what about a min one?"), _CONFIG)
    assert result == {}


@pytest.mark.asyncio
async def test_condense_is_not_handed_a_conversation_to_continue(
    _engine: AsyncEngine,
) -> None:
    """Prior turns must arrive as quoted transcript, not as chat messages.

    Standing the model at the end of a real Human/AI exchange is what let its
    reply-as-the-assistant instinct override the instruction to rewrite. The whole
    history must therefore reach it inside a single message.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    captured: list[Any] = []

    class CapturingStructured(FakeChatModel):
        def with_structured_output(self, *a: Any, **k: Any) -> Any:  # type: ignore[override]
            runnable = super().with_structured_output(*a, **k)

            async def _ainvoke(msgs: Any, *_a: Any, **_k: Any) -> Any:
                captured.extend(msgs)
                return self._next()

            runnable.ainvoke = _ainvoke  # type: ignore[method-assign]
            return runnable

    model = CapturingStructured(
        responses=[CondensedQuestion(is_follow_up=True, standalone_question="x")]
    )
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    await nodes["condense"](_condense_state("what about a min one?"), _CONFIG)

    # Exactly two messages: the system prompt and one user message holding everything.
    assert len(captured) == 2, [type(m).__name__ for m in captured]
    payload = _message_text(captured[1])
    assert "User: what is a heap?" in payload
    assert "Assistant: A heap is a tree-based structure." in payload
    assert "Latest message: what about a min one?" in payload


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
    # Marked so replayed history can tell this grounded answer apart from the
    # agent node's intermediate messages (see FINAL_ANSWER_KEY in state.py).
    assert ai_msg.additional_kwargs.get(FINAL_ANSWER_KEY) is True


@pytest.mark.asyncio
async def test_agent_does_not_mark_a_draft_that_still_needs_generate(
    _engine: AsyncEngine,
) -> None:
    """An agent draft that will still pass through generate must NOT be marked.

    Marking it would persist two final answers for one turn, which get_detail
    surfaces as two assistant bubbles for a single question. Here context is
    non-empty (a document was read), so generate runs and owns the answer.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(responses=[AIMessage("Draft summary of the document.")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("summarise lecture 1")],
        "question": "summarise lecture 1",
        "context": [{"chunk_id": "c1", "content": "doc text"}],
        "retry_count": 0,
    }
    result = await nodes["agent"](state, _CONFIG)

    assert not result["messages"][-1].additional_kwargs.get(FINAL_ANSWER_KEY)


@pytest.mark.asyncio
async def test_agent_marks_a_conversational_reply_as_the_final_answer(
    _engine: AsyncEngine,
) -> None:
    """A greeting ends the turn at the agent, so its reply IS the turn's answer.

    Without the marker the SSE layer would emit no token (leaving the bubble blank,
    and deleting the new conversation row as an orphan) and get_detail would hide
    the greeting from replayed history.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    model = FakeChatModel(responses=[AIMessage("Hello! How can I help with your notes?")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    state: dict[str, Any] = {
        "messages": [HumanMessage("hi")],
        "question": "hi",
        "context": [],
        "searched": False,
        "retry_count": 0,
    }
    result = await nodes["agent"](state, _CONFIG)

    marked = result["messages"][-1]
    assert marked.additional_kwargs.get(FINAL_ANSWER_KEY) is True
    # Explicitly empty, not absent: absent means "answered before citations were
    # persisted" to get_detail's legacy path, which would show stale sources.
    assert marked.additional_kwargs.get(CITATIONS_KEY) == []


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


@pytest.mark.asyncio
async def test_recent_drops_earlier_agent_direct_answers(_engine: AsyncEngine) -> None:
    """An agent direct answer from an EARLIER turn must not be replayed as history.

    It was written from the model's own knowledge (ungrounded) and is never shown
    to the user, so feeding it back invites later answers to lean on it. Only the
    marked `generate` answers are real conversation history.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    captured_msgs: list[Any] = []

    class CapturingFake(FakeChatModel):
        async def _agenerate(self, messages: Any, **kw: Any) -> Any:  # type: ignore[override]
            captured_msgs.extend(messages)
            return await super()._agenerate(messages, **kw)

    model = CapturingFake(responses=[AIMessage("new answer")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    grounded = AIMessage("Grounded answer from notes.")
    grounded.additional_kwargs[FINAL_ANSWER_KEY] = True
    state: dict[str, Any] = {
        "messages": [
            HumanMessage("first question"),
            AIMessage("Ungrounded chatter the agent invented."),  # agent, untagged
            grounded,
            HumanMessage("second question"),
        ],
        "question": "second question",
        "context": [],
        "retry_count": 0,
    }
    await nodes["generate"](state, _CONFIG)

    full_text = " ".join(str(getattr(m, "content", "")) for m in captured_msgs)
    assert "Ungrounded chatter" not in full_text
    assert "Grounded answer from notes." in full_text


@pytest.mark.asyncio
async def test_recent_keeps_a_marked_conversational_reply(
    _engine: AsyncEngine,
) -> None:
    """A conversational reply is a real answer, so it stays in replayed history.

    It survives on its own merits — the final-answer marker the agent stamps on it —
    rather than via any positional exemption. Without this, a greeting would vanish
    from the history that later turns are conditioned on.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    captured_msgs: list[Any] = []

    class CapturingFake(FakeChatModel):
        async def _agenerate(self, messages: Any, **kw: Any) -> Any:  # type: ignore[override]
            captured_msgs.extend(messages)
            return await super()._agenerate(messages, **kw)

    model = CapturingFake(responses=[AIMessage("Hello!")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=20, max_retries=2)

    greeting = AIMessage("Hello! How can I help?")
    greeting.additional_kwargs[FINAL_ANSWER_KEY] = True  # stamped by the agent node
    state: dict[str, Any] = {
        "messages": [HumanMessage("hi"), greeting, HumanMessage("what is a heap?")],
        "question": "what is a heap?",
        "context": [],
        "retry_count": 0,
    }
    await nodes["generate"](state, _CONFIG)

    full_text = " ".join(str(getattr(m, "content", "")) for m in captured_msgs)
    assert "Hello! How can I help?" in full_text


@pytest.mark.asyncio
async def test_recent_never_orphans_a_tool_message(_engine: AsyncEngine) -> None:
    """A naive last-N history trim can land between a tool-calling AIMessage and
    its ToolMessage response, producing a message list some providers (OpenAI)
    reject outright with a 400 ("messages with role 'tool' must be a response
    to a preceding message with 'tool_calls'") — confirmed in production after
    switching from Gemini (which tolerated it) to OpenAI. history_limit=2 forces
    the cut to land exactly inside the pair below; the trimmed messages must
    never start with (or contain) a ToolMessage lacking its preceding AIMessage."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    captured_msgs: list[Any] = []

    class CapturingFake(FakeChatModel):
        async def _agenerate(self, messages: Any, **kw: Any) -> Any:  # type: ignore[override]
            captured_msgs.extend(messages)
            return await super()._agenerate(messages, **kw)

    model = CapturingFake(responses=[AIMessage("answer")])
    tools = build_tools(FakeEmbeddingsProvider(DIM), maker, default_top_k=5)
    nodes = make_nodes(model, tools, history_limit=2, max_retries=2)

    tool_call = AIMessage(
        content="", tool_calls=[{"name": "retrieve_notes", "args": {"query": "x"}, "id": "tc1"}]
    )
    tool_result = ToolMessage(content="ctx", tool_call_id="tc1", name="retrieve_notes")
    # 4 messages, history_limit=2 → naive slice keeps only [tool_result, q2],
    # orphaning tool_result from its preceding tool_call.
    state: dict[str, Any] = {
        "messages": [HumanMessage("q1"), tool_call, tool_result, HumanMessage("q2")],
        "question": "q2",
        "context": [],
        "retry_count": 0,
    }
    await nodes["generate"](state, _CONFIG)

    for i, m in enumerate(captured_msgs):
        if isinstance(m, ToolMessage):
            preceding = captured_msgs[i - 1] if i > 0 else None
            assert isinstance(preceding, AIMessage) and preceding.tool_calls, (
                f"Orphaned ToolMessage at index {i}: "
                f"{[type(x).__name__ for x in captured_msgs]}"
            )


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


def test_route_after_agent_to_generate_when_context_is_citable() -> None:
    """An agent draft written after reading a document must still pass through
    generate — that is what turns it into a cited answer."""
    state: dict[str, Any] = {
        "messages": [AIMessage("draft summary")],
        "context": [{"chunk_id": "c1", "content": "doc text"}],
    }
    assert route_after_agent(state) == "generate"  # type: ignore[arg-type]


def test_route_after_agent_to_generate_when_search_found_nothing() -> None:
    """The anti-hallucination guarantee: once the notes have been searched and come
    back empty, the turn must reach generate's refusal rather than ending on whatever
    the agent decided to say. Empty context alone cannot distinguish this from a
    greeting — `searched` is what separates them."""
    state: dict[str, Any] = {
        "messages": [AIMessage("I think a weak entity is...")],
        "context": [],
        "searched": True,
    }
    assert route_after_agent(state) == "generate"  # type: ignore[arg-type]


def test_route_after_agent_ends_turn_on_conversational_reply() -> None:
    """A greeting needs no grounding pass. Routing it to generate is what produced
    "I couldn't find this in your notes." in reply to "hi": generate has empty context
    and its contract tells it to refuse."""
    state: dict[str, Any] = {
        "messages": [AIMessage("Hello! How can I help?")],
        "context": [],
        "searched": False,
    }
    assert route_after_agent(state) == "end"  # type: ignore[arg-type]


def test_route_after_agent_ends_turn_after_listing_documents() -> None:
    """"What notes do I have?" runs list_documents, which populates no context because
    a file listing has nothing citable. Before the conversational route existed this
    reached generate and was refused, exactly like a greeting."""
    state: dict[str, Any] = {
        "messages": [AIMessage("You have 3 documents: ...")],
        "context": [],
        "searched": False,  # list_documents is not a search
    }
    assert route_after_agent(state) == "end"  # type: ignore[arg-type]


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
