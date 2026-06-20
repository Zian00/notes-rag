"""The 3 user-scoped tools the agent can call.

Tools are closures over shared infra (embeddings + sessionmaker) built once at startup.
Per-request context (user_id, filters, top_k) flows in through LangGraph's
``config["configurable"]`` and is read via the auto-injected ``RunnableConfig`` — it is
NOT exposed to the LLM schema, so the model can never widen scope beyond the caller's
own data rows.

Each tool opens its OWN short-lived async session (not the request's) so DB access is
safe inside the long-lived compiled graph and during SSE streaming responses.
"""

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.rag.embeddings import EmbeddingsProvider
from app.services.retrieval import RetrievalService

# Type alias for clarity in function signatures.
SessionMaker = async_sessionmaker


def _user_id(config: RunnableConfig) -> uuid.UUID:
    """Extract the per-request user_id from the LangGraph runnable config."""
    return (config.get("configurable") or {})["user_id"]


def _chunk_to_dict(c: Any) -> dict[str, Any]:
    """Normalise a ChunkSearchResult into the shared citation/context shape.

    Used for both grounding context (sent to the LLM) and the final citations event
    (sent to the client) — one canonical shape prevents field-name mismatches.
    """
    return {
        "chunk_id": str(c.chunk_id),
        "document_id": str(c.document_id),
        "filename": c.filename,
        "title": c.title,
        "content": c.content,
        "page_number": c.page_number,
        "section": c.section,
        "score": getattr(c, "score", None),
    }


def build_tools(
    embeddings: EmbeddingsProvider,
    sessionmaker: SessionMaker,
    default_top_k: int,
) -> list[BaseTool]:
    """Build the 3 user-scoped tools, closing over shared infra.

    The returned tools are passed to ``model.bind_tools(...)`` so the agent can call
    them. ``config: RunnableConfig`` is auto-injected by langchain-core (hidden from the
    LLM schema) — confirmed working in langchain-core >= 0.3.
    """

    @tool
    async def retrieve_notes(
        query: str,
        course: str | None = None,
        tags: list[str] | None = None,
        *,
        config: RunnableConfig,
    ) -> list[dict[str, Any]]:
        """Search the user's lecture notes for passages relevant to the query.
        Use for questions and topic summaries. Optionally narrow by course or tags."""
        cfg = config.get("configurable") or {}
        async with sessionmaker() as session:
            service = RetrievalService(ChunkRepository(session), embeddings, default_top_k)
            results = await service.search(
                _user_id(config),
                query,
                top_k=cfg.get("top_k"),
                course=course or cfg.get("course"),
                tags=tags or cfg.get("tags"),
            )
        return [_chunk_to_dict(r) for r in results]

    @tool
    async def list_documents(
        course: str | None = None,
        *,
        config: RunnableConfig,
    ) -> list[dict[str, Any]]:
        """List the user's uploaded documents (id, title, filename, course).
        Use to answer 'what notes do I have?' or to find a document's id before summarising it."""
        async with sessionmaker() as session:
            docs = await DocumentRepository(session).list_for_user(
                _user_id(config), course=course
            )
        return [
            {
                "document_id": str(d.id),
                "title": d.title,
                "filename": d.filename,
                "course": d.course,
            }
            for d in docs
        ]

    @tool
    async def get_document_content(
        document_id: str,
        *,
        config: RunnableConfig,
    ) -> list[dict[str, Any]]:
        """Fetch the full text of one document, in order, for a whole-document summary.
        Pass a document_id from list_documents."""
        async with sessionmaker() as session:
            chunks = await ChunkRepository(session).get_for_document(
                uuid.UUID(document_id), _user_id(config)
            )
        # Reuse the citation shape; score is N/A for a deliberate full-doc fetch.
        return [
            {
                "chunk_id": str(c.id),
                "document_id": str(c.document_id),
                "filename": None,
                "title": c.section,
                "content": c.content,
                "page_number": c.page_number,
                "section": c.section,
                "score": None,
            }
            for c in chunks
        ]

    return [retrieve_notes, list_documents, get_document_content]


def format_chunks_for_llm(chunks: list[dict[str, Any]]) -> str:
    """Render retrieved chunks as a numbered, cite-able context block for the model.

    The numbers ([1], [2], …) match what the generate-node's prompt asks the model
    to use as inline citation markers.
    """
    if not chunks:
        return "NO RESULTS."
    lines = []
    for i, c in enumerate(chunks, 1):
        loc = f" (p.{c['page_number']})" if c.get("page_number") else ""
        title = c.get("title") or c.get("filename") or "note"
        lines.append(f"[{i}] {title}{loc}\n{c['content']}")
    return "\n\n".join(lines)
