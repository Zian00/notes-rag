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

from app.rag.graph.prompts import AGENT_SYSTEM, GENERATE_SYSTEM, GRADE_SYSTEM, REWRITE_SYSTEM
from app.rag.graph.state import RagState
from app.rag.graph.tools import format_chunks_for_llm

# Tools whose results populate ``context`` (used for grounding + citations).
_CONTEXT_TOOLS = {"retrieve_notes", "get_document_content"}

# Only retrieve_notes is graded — relevance grading makes sense only for similarity
# search that can return irrelevant results.  get_document_content is a deliberate
# whole-document fetch (never accidental), so it routes back to the agent, not grade.
_GRADE_TOOLS = {"retrieve_notes"}


class Grade(BaseModel):
    """Structured verdict from the grader LLM."""

    relevant: bool = Field(description="True if the context can answer the question.")


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
        """Trim message history to the last history_limit messages."""
        return state["messages"][-history_limit:]

    async def agent(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Decide whether to call a tool or answer directly.

        The LLM receives a system prompt + recent conversation history. If it decides to
        call a tool, the returned AIMessage has ``tool_calls``; otherwise it's a direct
        answer that goes straight to generate.
        """
        msgs = [SystemMessage(AGENT_SYSTEM), *_recent(state)]
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
        return {"relevant": verdict.relevant}

    async def rewrite(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Rewrite the question for better retrieval; increment the retry counter.

        The rewritten question is added to messages as a HumanMessage so the agent
        sees it and can call retrieve_notes again with the improved query.
        """
        resp = await model.ainvoke(
            [SystemMessage(REWRITE_SYSTEM), HumanMessage(state["question"])], config
        )
        new_q = resp.content if isinstance(resp.content, str) else str(resp.content)
        return {
            "question": new_q,
            "retry_count": state.get("retry_count", 0) + 1,
            "messages": [HumanMessage(new_q)],
        }

    async def generate(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        """Produce the final grounded answer from context.

        The generate prompt strictly forbids using knowledge outside the provided context
        — the anti-hallucination contract. If context is empty the model is told "NO
        RESULTS." and must reply with the refusal phrase.
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
        return {"messages": [resp]}

    return {
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
