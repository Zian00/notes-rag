"""Chat orchestration: conversation lifecycle + streaming the graph as SSE events.

Why this service uses its OWN async_sessionmaker (not the request session):
A FastAPI StreamingResponse keeps consuming this async generator *after* the
request's dependency-injected DB session has already been closed by FastAPI's
cleanup hooks.  Using a request-scoped session would cause use-after-close errors
mid-stream.  Instead, we open short-lived sessions ourselves for each DB touch so
no session outlives its own context manager.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.repositories.conversation import ConversationRepository

# Maximum characters used as the conversation title (derived from first question).
_TITLE_MAX = 120


class ConversationNotFound(Exception):
    """Raised when a conversation_id does not exist or does not belong to the caller."""


def _sse(event: str, data: Any) -> str:
    """Format a single Server-Sent Event frame: event + data lines, blank-line terminated."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _to_citations(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim context dicts to the citation-safe subset of keys for the client."""
    return [
        {k: c.get(k) for k in
         ("chunk_id", "document_id", "filename", "title", "page_number", "section", "score")}
        for c in context
    ]


class ChatService:
    def __init__(self, graph: Any, sessionmaker: async_sessionmaker[Any]) -> None:
        self._graph = graph
        self._sm = sessionmaker

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create(self, user_id: uuid.UUID, question: str) -> uuid.UUID:
        """Insert a new conversation row and return its id."""
        async with self._sm() as s:
            convo = await ConversationRepository(s).create(
                user_id=user_id, title=question[:_TITLE_MAX]
            )
            await s.commit()
            return convo.id

    async def verify_ownership(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Raise ConversationNotFound if the row does not exist or belongs to another user."""
        async with self._sm() as s:
            if await ConversationRepository(s).get_for_user(conversation_id, user_id) is None:
                raise ConversationNotFound(str(conversation_id))

    # ------------------------------------------------------------------
    # Streaming answer
    # ------------------------------------------------------------------

    async def stream_answer(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        question: str,
        course: str | None = None,
        tags: list[str] | None = None,
        top_k: int | None = None,
    ) -> AsyncIterator[str]:
        """Async generator yielding SSE frames: meta → token(s) → citations → done.

        On any exception inside the graph a single 'error' frame is yielded so the
        client always receives a well-formed stream.  ConversationNotFound is re-raised
        *before* yielding anything so the caller (the HTTP endpoint) can return a proper
        4xx instead.
        """
        # Ownership check / row creation before we yield anything.
        if conversation_id is None:
            conversation_id = await self._create(user_id, question)
        else:
            # Raises ConversationNotFound immediately (before the StreamingResponse begins)
            # so the endpoint can map it to a 404 if needed.
            await self.verify_ownership(conversation_id, user_id)

        # First SSE frame: tells the client which conversation this belongs to.
        yield _sse("meta", {"conversation_id": str(conversation_id)})

        # LangGraph config: thread_id drives the checkpointer (= conversation id);
        # user_id + filters are available to tools via config["configurable"].
        config: dict[str, Any] = {"configurable": {
            "thread_id": str(conversation_id),
            "user_id": user_id,
            "course": course,
            "tags": tags,
            "top_k": top_k,
        }}
        inputs: dict[str, Any] = {
            "messages": [HumanMessage(question)],
            "question": question,
            "retry_count": 0,
        }

        try:
            # stream_mode="messages" yields (msg, metadata) tuples; we only emit tokens
            # from the "generate" node so the client doesn't see intermediate tool messages.
            async for msg, metadata in self._graph.astream(inputs, config, stream_mode="messages"):
                if metadata.get("langgraph_node") == "generate" and getattr(msg, "content", ""):
                    yield _sse("token", {"delta": msg.content})

            # After streaming, fetch the final state to extract grounding sources.
            state = await self._graph.aget_state(config)
            yield _sse("citations", _to_citations(state.values.get("context", [])))

            # Bump updated_at so this conversation rises to the top of the user's list.
            async with self._sm() as s:
                await ConversationRepository(s).touch(conversation_id)
                await s.commit()

            yield _sse("done", {})

        except Exception as exc:
            # Surface a clean error frame so the client stream always terminates gracefully.
            yield _sse("error", {"detail": str(exc)})

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    async def list_conversations(self, user_id: uuid.UUID) -> list[Any]:
        async with self._sm() as s:
            return await ConversationRepository(s).list_for_user(user_id)

    async def get_detail(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
        """Return the conversation ORM object + message history from the checkpointer state.

        History maps AIMessage → 'assistant', HumanMessage → 'user'; other message types
        (ToolMessage, SystemMessage) are skipped because they are internal graph plumbing,
        not conversation turns the user would recognise.  Per-message citations are also
        omitted here — only the latest-turn context is persisted in state; citations are
        delivered live in the SSE stream.
        """
        async with self._sm() as s:
            convo = await ConversationRepository(s).get_for_user(conversation_id, user_id)
        if convo is None:
            raise ConversationNotFound(str(conversation_id))

        state = await self._graph.aget_state(
            {"configurable": {"thread_id": str(conversation_id)}}
        )
        messages: list[dict[str, str]] = []
        for m in state.values.get("messages", []):
            # content is str | list; we only include non-empty string content.
            content = m.content if isinstance(m.content, str) else ""
            if isinstance(m, AIMessage) and content:
                messages.append({"role": "assistant", "content": content})
            elif isinstance(m, HumanMessage) and content:
                messages.append({"role": "user", "content": content})
        return {"conversation": convo, "messages": messages}

    async def delete_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Delete the conversation row (ownership-checked) and best-effort checkpointer cleanup."""
        await self.verify_ownership(conversation_id, user_id)
        async with self._sm() as s:
            await ConversationRepository(s).delete(conversation_id)
            await s.commit()

        # adelete_thread is a version-dependent method on the checkpointer.
        # We call it best-effort: if not present or if it raises, the orphaned checkpoint
        # rows are harmless (they can't be accessed without the conversation row).
        deleter = getattr(self._graph.checkpointer, "adelete_thread", None)
        if deleter is not None:
            try:
                await deleter(str(conversation_id))
            except Exception:
                pass  # best-effort; orphaned checkpoint rows are benign
