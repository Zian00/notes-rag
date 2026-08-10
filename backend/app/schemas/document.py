import uuid
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    # from_attributes=True lets Pydantic read values from ORM model attributes
    # directly (e.g. doc.id instead of {"id": ...}), so routes can pass ORM
    # objects without manual conversion.
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    title: str | None
    course: str | None
    tags: list[str]
    content_type: str
    page_count: int | None
    chunk_count: int
    status: str
    error_message: str | None
    file_size: int
    embedding_model: str
    embedding_dimension: int
    created_at: datetime
    updated_at: datetime


class DuplicateDocumentResponse(BaseModel):
    """Documents the 409 response body when a file has already been ingested."""

    detail: str = "Document already exists"
    document_id: UUID


class ReplaceDocumentResponse(BaseModel):
    """Response body for POST /documents/{id}/replace.

    ``no_changes`` is True when the uploaded bytes hash-match the existing
    document, in which case no background reprocessing job was enqueued.
    """

    document: DocumentResponse
    no_changes: bool


class SearchRequest(BaseModel):
    # min_length=1 rejects empty strings; top_k bounded 1–50 to prevent
    # overly large result sets that would balloon memory and latency.
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    # Scope search to one group's documents; None searches all the user's docs.
    group_id: uuid.UUID | None = None
    tags: list[str] | None = None


class ChunkMatch(BaseModel):
    """One search hit returned by the retrieval endpoint."""

    chunk_id: UUID
    document_id: UUID
    filename: str
    title: str | None
    content: str
    page_number: int | None
    section: str | None
    score: float
