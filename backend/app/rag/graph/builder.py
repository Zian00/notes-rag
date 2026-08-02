"""Assemble the LangGraph RAG graph from nodes, routers, and tools.

``build_rag_graph`` is the single public function. It returns a compiled
``CompiledStateGraph`` that is stored on ``app.state`` at startup and shared
across all requests.

Both paths start with ``condense`` (resolves follow-up references using prior turns,
skipped on a conversation's first message), then diverge depending on
``settings.agentic_retrieval``:

- **Agentic (default, True):** START → condense → agent → (tool call? tools : generate).
  The LLM decides when and which tool to invoke. ``retrieve_notes`` results are
  graded; weak results trigger a query rewrite (bounded by ``max_grade_retries``).

- **Linear fallback (False):** START → condense → triage → (needs notes? force_retrieve
  → tools → grade → ... → generate : chat → END). For weak/local LLMs that can't
  reliably call tools: always retrieve first, skip the agent's tool-decision, preserve
  grade → rewrite → generate. Useful when the model struggles with tool-call formatting.
  `triage` replaces the agent's tool-call decision as the point where a conversational
  turn is separated from one that needs the notes.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.rag.embeddings import EmbeddingsProvider
from app.rag.graph.nodes import (
    make_nodes,
    make_route_after_grade,
    route_after_agent,
    route_after_tools,
    route_after_triage,
)
from app.rag.graph.state import RagState
from app.rag.graph.tools import build_tools


def build_rag_graph(
    chat_model: BaseChatModel,
    embeddings: EmbeddingsProvider,
    sessionmaker: async_sessionmaker,  # caller provides an AsyncSession-based sessionmaker
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
) -> object:
    """Compile and return the RAG StateGraph.

    Args:
        chat_model: A LangChain BaseChatModel (supports bind_tools + with_structured_output).
        embeddings: EmbeddingsProvider for semantic search inside the retrieve tool.
        sessionmaker: The app's async_sessionmaker; tools open short-lived sessions from it.
        settings: Application settings (retrieval_top_k, agentic_retrieval, etc.).
        checkpointer: A LangGraph checkpoint saver (InMemorySaver for tests;
            AsyncPostgresSaver in prod).

    Returns:
        A compiled LangGraph graph ready for ``.ainvoke`` / ``.astream``.
    """
    tools = build_tools(embeddings, sessionmaker, settings.retrieval_top_k)
    nodes = make_nodes(chat_model, tools, settings.chat_history_limit, settings.max_grade_retries)
    route_after_grade = make_route_after_grade(settings.max_grade_retries)

    g = StateGraph(RagState)

    # Register all nodes from the make_nodes factory.
    for name, fn in nodes.items():
        g.add_node(name, fn)

    # Both paths run `condense` first: it resolves follow-up references ("what about
    # that?") into a standalone question using prior turns, skipped on a conversation's
    # first message. `rewrite`'s loop-back edges bypass it — condense is a turn-start
    # operation, not something that should re-run on every corrective retry.
    g.add_edge(START, "condense")

    if settings.agentic_retrieval:
        # --- Agentic path: LLM decides which tool to call (or to answer directly). ---
        g.add_edge("condense", "agent")
        g.add_conditional_edges(
            "agent",
            route_after_agent,
            # "end": a conversational reply (greeting, "what notes do I have?") is
            # already the final answer — see route_after_agent.
            {"tools": "tools", "generate": "generate", "end": END},
        )
        g.add_conditional_edges(
            "tools", route_after_tools, {"grade": "grade", "agent": "agent"}
        )
    else:
        # --- Linear fallback: always retrieve, skip the agent tool-call decision. ---
        # A tiny seed node synthesises a forced retrieve_notes call so tools_node can
        # execute it without the agent model deciding what to do.
        async def force_retrieve(state: RagState, config: RunnableConfig) -> dict[str, Any]:
            """Inject a retrieve_notes tool call without asking the LLM."""
            call = {
                "name": "retrieve_notes",
                "args": {"query": state["question"]},
                "id": "seed",
            }
            return {"messages": [AIMessage(content="", tool_calls=[call])]}

        g.add_node("force_retrieve", force_retrieve)
        # Triage stands in for the tool-call decision the agent makes on the other path.
        # Without it every message force-retrieves, so "hi" runs a vector search, the
        # grader rejects the results, and generate refuses a greeting.
        g.add_edge("condense", "triage")
        g.add_conditional_edges(
            "triage", route_after_triage, {"retrieve": "force_retrieve", "chat": "chat"}
        )
        g.add_edge("chat", END)
        g.add_edge("force_retrieve", "tools")
        # tools always produces retrieve_notes output → grade directly.
        g.add_edge("tools", "grade")

    # Grade → rewrite or generate (shared by both paths).
    g.add_conditional_edges(
        "grade", route_after_grade, {"generate": "generate", "rewrite": "rewrite"}
    )
    # Rewrite loops back to the decision point for the active path.
    g.add_edge("rewrite", "agent" if settings.agentic_retrieval else "force_retrieve")
    g.add_edge("generate", END)

    return g.compile(checkpointer=checkpointer)
