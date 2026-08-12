from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages

# additional_kwargs marker set by the `generate` node on the one AIMessage that is
# the real, context-grounded answer for a turn.
#
# Why this is needed: `agent` also writes AIMessages into state — a tool-call
# decision, or (when it judges no notes are needed) a direct answer written from
# the model's own knowledge. Live SSE streaming only ever emits the `generate`
# node's tokens, but conversation history is replayed from checkpointer state,
# where both nodes' messages sit side by side. Without this marker, replayed
# history shows the agent's ungrounded text as though it were an answer — the
# exact thing GENERATE_SYSTEM's grounding contract exists to prevent.
FINAL_ANSWER_KEY = "notes_rag_final_answer"

# additional_kwargs key holding the citations backing a final answer.
#
# Citations are delivered live over SSE, but the SSE frame is transient — replayed
# history needs its own copy or reopening a conversation loses every source. Riding
# along on the answer message (rather than a separate table) reuses storage the
# checkpointer already persists, keeps each answer bound to its own sources, and
# avoids a second source of truth for message data.
CITATIONS_KEY = "notes_rag_citations"

# additional_kwargs key on HumanMessage: the document IDs the user attached
# (uploaded from the chat composer) on this turn. Display-only — retrieval
# stays group-scoped and never reads this; it exists so replayed history can
# render "what was uploaded on this turn" as a card above the user's bubble.
ATTACHED_DOCUMENTS_KEY = "notes_rag_attached_document_ids"


class RagState(TypedDict, total=False):
    # Conversation messages. add_messages appends (and deduplicates by id);
    # the checkpointer persists this across turns keyed by thread_id.
    messages: Annotated[list[AnyMessage], add_messages]
    question: str  # current (possibly rewritten) query
    context: list[dict[str, Any]]  # chunks from the latest retrieve/get-document call
    # True when the grader judged the chunks CURRENTLY in `context` unable to answer
    # the question. Scoped to those chunks, not to the turn: any tool that replaces
    # `context` clears this, because the verdict described the chunks it was shown.
    # A turn-scoped verdict would outlive its subject — a rejected search followed by
    # a document fetch would discard the perfectly good document.
    context_rejected: bool
    # True once retrieve_notes has run in THIS turn. Distinguishes "the notes were
    # searched and came back empty" (must refuse — the anti-hallucination contract)
    # from "no search was ever needed" (a greeting, or listing what documents exist),
    # which look identical from `context` alone since both leave it empty.
    searched: bool
    # Named grade_reason, not reason: RagState is one flat namespace shared by every
    # node's return patch, so a bare "reason" would invite collisions with any future
    # per-node reason field. Grader's explanation for the verdict; consumed by rewrite.
    grade_reason: str
    retry_count: int  # number of rewrites so far (capped at max_grade_retries)
    # Linear path only: triage's verdict on whether this turn needs the notes at all.
    # The agentic path has no use for it — its agent makes the same call by choosing
    # whether to invoke a tool.
    needs_notes: bool


def new_turn_inputs(
    question: str,
    attached_document_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Graph input for one turn: the new message plus a clean slate for the rest.

    Lives beside the field declarations, not in the calling service, so that adding a
    derived field to RagState and forgetting to reset it is a one-file mistake rather
    than a silent one. That exact omission is what let a greeting inherit the previous
    turn's chunks and be answered with "I couldn't find this in your notes."

    Only `messages` survives across turns — the checkpointer accumulates it via the
    add_messages reducer. Everything else describes one turn's retrieval attempt and
    must not leak into the next.
    """
    kwargs: dict[str, Any] = {}
    if attached_document_ids:
        kwargs[ATTACHED_DOCUMENTS_KEY] = attached_document_ids
    return {
        "messages": [HumanMessage(question, additional_kwargs=kwargs)],
        "question": question,
        "context": [],
        "context_rejected": False,
        "searched": False,
        "grade_reason": "",
        "retry_count": 0,
    }
