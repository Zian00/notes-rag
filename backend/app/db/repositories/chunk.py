import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.document import Document, DocumentChunk


@dataclass(frozen=True)
class ChunkSearchResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    title: str | None
    content: str
    page_number: int | None
    section: str | None
    score: float  # cosine similarity in [0, 1]; higher is closer


class ChunkRepository(BaseRepository[DocumentChunk]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DocumentChunk, session)

    async def add_many(self, rows: list[dict]) -> None:
        self._session.add_all([DocumentChunk(**row) for row in rows])
        await self._session.flush()

    async def search_similar(
        self,
        user_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int,
        course: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ChunkSearchResult]:
        distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                Document.filename,
                Document.title,
                DocumentChunk.content,
                DocumentChunk.page_number,
                DocumentChunk.section,
                distance,
            )
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.user_id == user_id)
        )
        if course is not None:
            stmt = stmt.where(Document.course == course)
        if tags:
            stmt = stmt.where(Document.tags.contains(tags))
        stmt = stmt.order_by(distance).limit(top_k)

        result = await self._session.execute(stmt)
        return [
            ChunkSearchResult(
                chunk_id=row.id,
                document_id=row.document_id,
                filename=row.filename,
                title=row.title,
                content=row.content,
                page_number=row.page_number,
                section=row.section,
                score=1.0 - float(row.distance),
            )
            for row in result.all()
        ]
