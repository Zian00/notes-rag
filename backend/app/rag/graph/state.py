from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
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


class RagState(TypedDict, total=False):
    # Conversation messages. add_messages appends (and deduplicates by id);
    # the checkpointer persists this across turns keyed by thread_id.
    messages: Annotated[list[AnyMessage], add_messages]
    question: str  # current (possibly rewritten) query
    context: list[dict[str, Any]]  # chunks from the latest retrieve/get-document call
    relevant: bool  # grade verdict: is context relevant to the question?
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
