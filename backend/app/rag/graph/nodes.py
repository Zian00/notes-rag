"""LangGraph nodes and routing functions for the agentic RAG graph.

Each node is an async callable ``(state, config) -> dict`` that returns a state patch.
The ``make_nodes`` factory closes over the shared LLM + tools, then returns a dict of
node callables that the builder wires into the ``StateGraph``.

Routers are plain synchronous functions on ``RagState`` — easy to unit-test in isolation
without running the full graph.
"""

from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END
from pydantic import BaseModel, Field

from app.rag.graph.prompts import (
    AGENT_SYSTEM,
    CONDENSE_SYSTEM,
    GENERATE_SYSTEM,
    GRADE_SYSTEM,
    REWRITE_SYSTEM,
)
from app.rag.graph.state import CITATIONS_KEY, FINAL_ANSWER_KEY, RagState
from app.rag.graph.tools import format_chunks_for_llm
from app.services.citations import to_citations

# Tools whose results populate ``context`` (used for grounding + citations).
_CONTEXT_TOOLS = {"retrieve_notes", "get_document_content"}

# Only retrieve_notes is graded — relevance grading makes sense only for similarity
# search that can return irrelevant results.  get_document_content is a deliberate
# whole-document fetch (never accidental), so it routes back to the agent, not grade.
_GRADE_TOOLS = {"retrieve_notes"}

# A turn's messages list always contains at least the just-added HumanMessage; anything
# at or below this means there is no prior turn yet, so condense has nothing to resolve.
_MIN_MESSAGES_FOR_CONDENSE = 2


def _message_text(resp: Any) -> str:
    """Extract plain text from an LLM response, whatever its content shape."""
    return resp.content if isinstance(resp.content, str) else str(resp.content)


class Grade(BaseModel):
    """Structured verdict from the grader LLM."""

    relevant: bool = Field(description="True if the context can answer the question.")
    reason: str = Field(
        description="One sentence: why the context is or isn't sufficient — "
        "what's missing or mismatched, if not relevant."
    )


def make_nodes(
    model: BaseChatModel,
    tools: list[BaseTool],
    history_limit: int,
    max_retries: int,
) -> dict[str, Any]:
    """Build all node callables, closing over the LLM + tools.

    Args:
        model: The chat model (with tool-calling + structured-output support).
        tools: The 3 user-scoped tools returned by ``build_tools``.
        history_limit: Max recent messages fed to the model (context-window guard).
        max_retries: Rewrite cap — after this many rewrites, generate anyway.

    Returns:
        Dict mapping node names to async callables for ``StateGraph.add_node``.
    """
    tools_by_name = {t.name: t for t in tools}
    # bind_tools makes the agent aware of available tools.
    agent_model = model.bind_tools(tools)
    # with_structured_output forces the grader to return a validated Grade object.
    grader = model.with_structured_output(Grade)

    def _recent(state: RagState) -> list[Any]:
        """Trim message history to the last history_limit messages.

        A naive last-N slice can land inside an AIMessage(tool_calls) +
        ToolMessage pairing, leaving an orphaned ToolMessage at the front with
        no preceding tool-calling message in the trimmed window. OpenAI
        strictly rejects any message list with such an orphan (confirmed via
        production 400s after switching providers — Gemini tolerated it).
        Dropping leading ToolMessage(s) left dangling by the cut never removes
        more than one AIMessage's worth of already-old tool results.
        """
        trimmed = state["messages"][-history_limit:]
        while trimmed and isinstance(trimmed[0], ToolMessage):
            trimmed = trimmed[1:]
        return trimmed

    async def condense(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Resolve follow-up references in the latest question using prior turns.

        Skipped on a conversation's first message: there is no prior turn to resolve
        references against, so the LLM call would be a pure latency cost with no effect.
        Only ``question`` changes here — ``messages`` is left untouched so the persisted
        transcript still reflects exactly what the user typed; ``agent`` surfaces the
        resolved question to its own tool-call reasoning via an ephemeral note instead
        (see below) rather than this node injecting a synthetic message into history.
        """
        if len(state["messages"]) < _MIN_MESSAGES_FOR_CONDENSE:
            return {}
        resp = await model.ainvoke([SystemMessage(CONDENSE_SYSTEM), *_recent(state)], config)
        return {"question": _message_text(resp)}

    async def agent(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Decide whether to call a tool or answer directly.

        The LLM receives a system prompt + recent conversation history. If it decides to
        call a tool, the returned AIMessage has ``tool_calls``; otherwise it's a direct
        answer that goes straight to generate.

        The current ``question`` (possibly resolved by ``condense`` or rewritten by
        ``rewrite``) is appended as an ephemeral note — never persisted to ``messages`` —
        so tool-call reasoning uses the improved question without the visible conversation
        ever showing a query the user didn't literally type.
        """
        msgs = [SystemMessage(AGENT_SYSTEM), *_recent(state)]
        if state.get("question"):
            msgs.append(HumanMessage(f"(Current question to answer: {state['question']})"))
        resp = await agent_model.ainvoke(msgs, config)
        return {"messages": [resp]}

    async def tools_node(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Execute every tool call in the last AIMessage, collect results.

        For retrieval tools (retrieve_notes, get_document_content) the raw result list
        is stored in ``context`` so the grade/generate nodes can use it. The ToolMessage
        carries a human-readable rendering so the agent sees what was retrieved.

        ``name`` is set explicitly on each ToolMessage because ``route_after_tools``
        reads ``.name`` to decide whether to grade — the field is not auto-filled.
        """
        # Cast to AIMessage: tools_node is only reached when route_after_agent returned
        # "tools", which requires tool_calls to be non-empty on the last AIMessage.
        from typing import cast

        last = cast(AIMessage, state["messages"][-1])
        out_msgs: list[Any] = []
        context = state.get("context", [])

        for tc in last.tool_calls:
            result = await tools_by_name[tc["name"]].ainvoke(tc["args"], config=config)
            # Context tools return chunk dicts with a "content" key → format for the LLM.
            # list_documents returns plain dicts (no "content") → use str repr.
            if tc["name"] in _CONTEXT_TOOLS and isinstance(result, list):
                tool_content = format_chunks_for_llm(result)
                context = result  # latest context replaces prior context
            else:
                tool_content = str(result)
            out_msgs.append(
                ToolMessage(
                    content=tool_content,
                    tool_call_id=tc["id"],
                    name=tc["name"],  # required by route_after_tools
                )
            )

        return {"messages": out_msgs, "context": context}

    async def grade(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Ask the LLM to judge whether the context can answer the question.

        Returns a patch with ``relevant`` set to the grader's boolean verdict.
        """
        ctx = format_chunks_for_llm(state.get("context", []))
        raw = await grader.ainvoke(
            [
                SystemMessage(GRADE_SYSTEM),
                HumanMessage(f"Question: {state['question']}\n\nContext:\n{ctx}"),
            ],
            config,
        )
        # grader returns a Grade pydantic object; the type is Any in mypy due to
        # with_structured_output returning a broad union — cast for safety.
        verdict: Grade = raw  # type: ignore[assignment]
        return {"relevant": verdict.relevant, "grade_reason": verdict.reason}

    async def rewrite(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Rewrite the question for better retrieval, informed by why the last attempt failed.

        Unlike a blind rephrase, this sees grade's reason and the actual chunks that were
        retrieved-but-rejected, so it can pick up vocabulary mismatches (e.g. notes say
        "back-propagation", the question said "backprop") instead of guessing blind.
        Does not touch ``messages`` — see ``agent``'s ephemeral-note mechanism for how the
        rewritten question reaches the next tool-call decision without being persisted.
        """
        ctx = format_chunks_for_llm(state.get("context", []))
        reason = state.get("grade_reason", "")
        resp = await model.ainvoke(
            [
                SystemMessage(REWRITE_SYSTEM),
                HumanMessage(
                    f"Original question: {state['question']}\n\n"
                    f"Why the last search failed: {reason}\n\n"
                    f"What was retrieved instead:\n{ctx}"
                ),
            ],
            config,
        )
        return {
            "question": _message_text(resp),
            "retry_count": state.get("retry_count", 0) + 1,
        }

    async def generate(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Produce the final grounded answer from context.

        The generate prompt strictly forbids using knowledge outside the provided context
        — the anti-hallucination contract. If context is empty the model is told "NO
        RESULTS." and must reply with the refusal phrase.

        The reply is marked with FINAL_ANSWER_KEY so replayed conversation history can
        tell this grounded answer apart from `agent`'s intermediate messages (see the
        constant's docstring in state.py).
        """
        ctx = format_chunks_for_llm(state.get("context", []))
        resp = await model.ainvoke(
            [
                SystemMessage(GENERATE_SYSTEM),
                *_recent(state),
                HumanMessage(f"Context:\n{ctx}"),
            ],
            config,
        )
        resp.additional_kwargs[FINAL_ANSWER_KEY] = True
        # Derived from the same context stream_answer's live SSE citations frame uses,
        # via the same shared helper — the two can't disagree.
        resp.additional_kwargs[CITATIONS_KEY] = to_citations(state.get("context", []))
        return {"messages": [resp]}

    return {
        "condense": condense,
        "agent": agent,
        "tools": tools_node,
        "grade": grade,
        "rewrite": rewrite,
        "generate": generate,
    }


# ---------------------------------------------------------------------------
# Routers — plain functions so they can be unit-tested without a running graph.
# ---------------------------------------------------------------------------


def route_after_agent(state: RagState) -> Literal["tools", "generate"]:
    """If the agent emitted a tool call, dispatch to tools; else go straight to generate."""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "generate"


def route_after_tools(state: RagState) -> Literal["grade", "agent"]:
    """After tool execution: grade retrieve_notes results; send everything else back to the agent.

    Only retrieve_notes is graded — relevance grading only makes sense for similarity
    search that may return off-topic results.  get_document_content is a deliberate
    full-doc fetch and is never irrelevant, so it routes back to the agent.
    """
    last_tool = state["messages"][-1]
    name = getattr(last_tool, "name", None)
    return "grade" if name in _GRADE_TOOLS else "agent"


def make_route_after_grade(max_retries: int) -> Any:
    """Factory that captures max_retries in the returned router closure."""

    def route_after_grade(state: RagState) -> Literal["generate", "rewrite"]:
        """Relevant → generate; weak + retries left → rewrite; retries exhausted → generate."""
        if state.get("relevant"):
            return "generate"
        if state.get("retry_count", 0) < max_retries:
            return "rewrite"
        return "generate"  # answer honestly from weak/empty context

    return route_after_grade


# Exposed for use as the terminal sentinel in builder conditional edges.
__all__ = [
    "Grade",
    "make_nodes",
    "route_after_agent",
    "route_after_tools",
    "make_route_after_grade",
    "END",
]
