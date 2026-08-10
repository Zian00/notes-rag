import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Rename bound: max 120 matches the conversations.title column (String(120)); strip +
# min_length=1 rejects blank/whitespace-only titles.
ConversationTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


class ChatRequest(BaseModel):
    # min_length=1 rejects empty questions; top_k bounded 1–20 (narrower than search's 50).
    question: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    # Group scope is honored ONLY when creating a new conversation (conversation_id is
    # None). For an existing conversation the stored conversation.group_id wins, so the
    # client can never widen or change a chat's scope after creation.
    group_id: uuid.UUID | None = None
    tags: list[str] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


class Citation(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str | None = None
    title: str | None = None
    page_number: int | None = None
    section: str | None = None
    score: float | None = None


class ConversationResponse(BaseModel):
    # from_attributes=True lets Pydantic read ORM objects directly (e.g. convo.id).
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    group_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ConversationUpdate(BaseModel):
    """PATCH body: rename and/or move a conversation.

    PATCH semantics — a field is only touched when the client actually sends it
    (tracked via model_fields_set): omitting `group_id` leaves the group as-is,
    while sending `group_id: null` explicitly moves the chat to ungrouped.
    """

    title: ConversationTitle | None = None
    group_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "ConversationUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide title and/or group_id.")
        return self


class MessageResponse(BaseModel):
    role: str
    content: str
    # Assistant turns only, and only for threads answered after citations began being
    # persisted onto the answer message — null everywhere else.
    citations: list[Citation] | None = None


class ConversationDetail(ConversationResponse):
    messages: list[MessageResponse]
