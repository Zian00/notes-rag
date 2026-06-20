from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class RagState(TypedDict, total=False):
    # Conversation messages. add_messages appends (and deduplicates by id);
    # the checkpointer persists this across turns keyed by thread_id.
    messages: Annotated[list[AnyMessage], add_messages]
    question: str  # current (possibly rewritten) query
    context: list[dict[str, Any]]  # chunks from the latest retrieve/get-document call
    relevant: bool  # grade verdict: is context relevant to the question?
    retry_count: int  # number of rewrites so far (capped at max_grade_retries)
