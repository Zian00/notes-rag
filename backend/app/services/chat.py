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
from app.rag.graph.state import CITATIONS_KEY, FINAL_ANSWER_KEY, new_turn_inputs
from app.services.citations import to_citations

# Maximum characters used as the conversation title (derived from first question).
_TITLE_MAX = 120


class ConversationNotFound(Exception):
    """Raised when a conversation_id does not exist or does not belong to the caller."""


def _sse(event: str, data: Any) -> str:
    """Format a single Server-Sent Event frame: event + data lines, blank-line terminated."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"




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
        # Track whether *this* call created the row so we can clean up on error.
        # (A conversation_id supplied by the caller already has committed data; we
        # must not delete it if that existing conversation errors on turn N.)
        created_this_call = conversation_id is None

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
        # Built in state.py, beside the field declarations, so a newly-added derived
        # field cannot be left out of the per-turn reset from over here.
        inputs: dict[str, Any] = new_turn_inputs(question)

        streamed_any = False  # becomes True once a token frame is yielded

        try:
            # Two stream modes, because the two kinds of answer arrive differently:
            #
            # "messages" — token-by-token from `generate`, the grounded-answer path.
            #   Filtered to that node so intermediate tool chatter never reaches the client.
            #
            # "updates" — whole state patches, emitted after a node finishes. This is how
            #   a conversational reply (greeting, doc listing) is sent: it comes from the
            #   `agent` node, which may also emit prose *before* deciding to call a tool.
            #   Streaming that node's tokens live would put such prose on screen before we
            #   could know a tool call was coming, and it can't be retracted. Waiting for
            #   the completed node lets us send only what the final-answer marker confirms
            #   is an answer. The client's own reveal buffer re-animates it, so a single
            #   frame still types out on screen.
            stream_modes = ["messages", "updates"]
            async for mode, payload in self._graph.astream(
                inputs, config, stream_mode=stream_modes
            ):
                if mode == "messages":
                    msg, metadata = payload
                    if metadata.get("langgraph_node") == "generate" and getattr(msg, "content", ""):
                        streamed_any = True
                        yield _sse("token", {"delta": msg.content})
                elif mode == "updates":
                    for node_name, patch in payload.items():
                        if node_name != "agent" or not isinstance(patch, dict):
                            continue
                        for m in patch.get("messages", []):
                            marked = getattr(m, "additional_kwargs", {}).get(FINAL_ANSWER_KEY)
                            if marked and getattr(m, "content", ""):
                                streamed_any = True
                                yield _sse("token", {"delta": m.content})

            # After streaming, fetch the final state to extract grounding sources.
            state = await self._graph.aget_state(config)
            yield _sse("citations", to_citations(state.values.get("context", [])))

            # Bump updated_at so this conversation rises to the top of the user's list.
            async with self._sm() as s:
                await ConversationRepository(s).touch(conversation_id)
                await s.commit()

            yield _sse("done", {})

        except Exception as exc:
            # If this call created the conversation row AND no tokens were streamed yet
            # (i.e. the turn produced no visible output), delete the orphan row so it
            # doesn't pollute GET /conversations.  If tokens already reached the client
            # we keep the row — the turn partially succeeded and the history is useful.
            if created_this_call and not streamed_any:
                try:
                    async with self._sm() as s:
                        await ConversationRepository(s).delete(conversation_id)
                        await s.commit()
                except Exception:
                    pass  # best-effort; an orphan row is preferable to swallowing the real error

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

        Only FINAL_ANSWER_KEY-marked AIMessages count as assistant turns: `agent` also
        writes AIMessages into state (tool-call decisions, or direct answers written from
        the model's own knowledge), and replaying those as answers would surface
        ungrounded text that live streaming never showed.  Threads checkpointed before
        that marker existed have none, so they fall back to showing every AIMessage —
        without it their history would replay with no assistant replies at all.
        """
        async with self._sm() as s:
            convo = await ConversationRepository(s).get_for_user(conversation_id, user_id)
        if convo is None:
            raise ConversationNotFound(str(conversation_id))

        state = await self._graph.aget_state(
            {"configurable": {"thread_id": str(conversation_id)}}
        )
        raw_messages = state.values.get("messages", [])
        is_legacy_thread = not any(
            isinstance(m, AIMessage) and m.additional_kwargs.get(FINAL_ANSWER_KEY)
            for m in raw_messages
        )
        messages: list[dict[str, Any]] = []
        for m in raw_messages:
            # content is str | list; we only include non-empty string content.
            content = m.content if isinstance(m.content, str) else ""
            if isinstance(m, AIMessage) and content:
                if is_legacy_thread or m.additional_kwargs.get(FINAL_ANSWER_KEY):
                    messages.append({
                        "role": "assistant",
                        "content": content,
                        # Absent on pre-CITATIONS_KEY threads — their sources were
                        # never stored, so there is nothing to recover.
                        "citations": m.additional_kwargs.get(CITATIONS_KEY),
                    })
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
