import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
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
        # Bulk-insert all chunks for a document in one go. flush() sends the INSERTs now
        # (so FKs/ids resolve) but does NOT commit — the service owns the transaction.
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
        # cosine_distance emits pgvector's `<=>` operator. Distance 0 = identical direction,
        # 2 = opposite; for our unit vectors it's in [0, 2]. We sort ascending (closest first).
        distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                Document.filename,  # joined in so the API can show the source
                Document.title,
                DocumentChunk.content,
                DocumentChunk.page_number,
                DocumentChunk.section,
                distance,
            )
            .join(Document, DocumentChunk.document_id == Document.id)
            # per-user isolation: never return another user's chunks
            .where(DocumentChunk.user_id == user_id)
        )
        # Optional metadata narrowing applied as plain SQL filters BEFORE the vector sort.
        if course is not None:
            stmt = stmt.where(Document.course == course)
        if tags:
            stmt = stmt.where(Document.tags.contains(tags))  # JSONB @> : doc has all given tags
        # order_by(distance) uses the labeled expression object (a bare "distance" string
        # would raise in SQLAlchemy 2.0). The HNSW index makes this ORDER BY fast.
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
                score=1.0 - float(row.distance),  # flip distance → similarity (higher = closer)
            )
            for row in result.all()
        ]

    async def get_for_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[DocumentChunk]:
        """All chunks of one document, in order — for whole-document summarisation.
        Scoped to the owner so a user can never fetch another user's document."""
        stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.user_id == user_id,
            )
            .order_by(DocumentChunk.chunk_index)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_hashes_for_document(self, document_id: uuid.UUID) -> dict[str, uuid.UUID]:
        """Maps each existing chunk's content_hash -> its row id, for one document —
        used by Replace to decide which chunks can be left alone vs deleted."""
        stmt = select(DocumentChunk.content_hash, DocumentChunk.id).where(
            DocumentChunk.document_id == document_id
        )
        result = await self._session.execute(stmt)
        return {row.content_hash: row.id for row in result.all()}

    async def update_chunk_position(
        self,
        chunk_id: uuid.UUID,
        *,
        chunk_index: int,
        page_number: int | None,
        section: str | None,
    ) -> None:
        """Repositions a RETAINED chunk (its content_hash matched an old chunk, so
        its embedding is still valid) to reflect where it sits in the newly
        reprocessed document."""
        chunk = await self.get(chunk_id)
        if chunk is None:
            return
        chunk.chunk_index = chunk_index
        chunk.page_number = page_number
        chunk.section = section
        await self._session.flush()

    async def delete_by_ids(self, chunk_ids: list[uuid.UUID]) -> None:
        if not chunk_ids:
            return
        stmt = delete(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
        await self._session.execute(stmt)
        await self._session.flush()
