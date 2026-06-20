import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    # min_length=1 rejects empty questions; top_k bounded 1–20 (narrower than search's 50).
    question: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    course: str | None = None
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
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    role: str
    content: str


class ConversationDetail(ConversationResponse):
    messages: list[MessageResponse]
