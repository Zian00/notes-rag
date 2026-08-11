import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, delete, literal_column, select, text
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
    # Meaning depends on how this result was produced: cosine similarity in [0, 1]
    # from search_similar, BM25 relevance from search_keyword, or (once a Reranker
    # has run) the cross-encoder's relevance score — see Reranker.rerank().
    score: float


class ChunkRepository(BaseRepository[DocumentChunk]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DocumentChunk, session)

    async def add_many(self, rows: list[dict]) -> None:
        # Bulk-insert all chunks for a document in one go. flush() sends the INSERTs now
        # (so FKs/ids resolve) but does NOT commit — the service owns the transaction.
        self._session.add_all([DocumentChunk(**row) for row in rows])
        await self._session.flush()

    def _base_chunk_query(
        self,
        user_id: uuid.UUID,
        score_expr: ColumnElement[float],
        group_id: uuid.UUID | None,
        tags: list[str] | None,
    ) -> Select[Any]:
        """Shared SELECT shape for both retrieval paths: same columns, join, and
        metadata filters — only the score expression and match predicate differ
        between search_similar (cosine distance) and search_keyword (BM25)."""
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                Document.filename,  # joined in so the API can show the source
                Document.title,
                DocumentChunk.content,
                DocumentChunk.page_number,
                DocumentChunk.section,
                score_expr,
            )
            .join(Document, DocumentChunk.document_id == Document.id)
            # per-user isolation: never return another user's chunks
            .where(DocumentChunk.user_id == user_id)
        )
        # Group scope is strict for every bucket, including "ungrouped": a chat with
        # group_id=None must see ONLY documents that are themselves ungrouped, not the
        # user's whole library. SQLAlchemy compiles `== None` to `IS NULL`, so this one
        # comparison correctly covers both the grouped and ungrouped cases.
        stmt = stmt.where(Document.group_id == group_id)
        if tags:
            stmt = stmt.where(Document.tags.contains(tags))  # JSONB @> : doc has all given tags
        return stmt

    async def search_similar(
        self,
        user_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int,
        group_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
    ) -> list[ChunkSearchResult]:
        # cosine_distance emits pgvector's `<=>` operator. Distance 0 = identical direction,
        # 2 = opposite; for our unit vectors it's in [0, 2]. We sort ascending (closest first).
        distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
        # order_by(distance) uses the labeled expression object (a bare "distance" string
        # would raise in SQLAlchemy 2.0). The HNSW index makes this ORDER BY fast.
        stmt = (
            self._base_chunk_query(user_id, distance, group_id, tags)
            .order_by(distance)
            .limit(top_k)
        )
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

    async def search_keyword(
        self,
        user_id: uuid.UUID,
        query: str,
        top_k: int,
        group_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
    ) -> list[ChunkSearchResult]:
        """BM25 keyword search via pg_search's bm25 index on document_chunks(content).

        The @@@ operator and paradedb.match/score() functions aren't part of
        SQLAlchemy's vocabulary, so they're injected as bound text() fragments.
        """
        if not query.strip():
            return []
        score_expr: ColumnElement[float] = literal_column(
            "paradedb.score(document_chunks.id)"
        ).label("score")
        stmt = (
            self._base_chunk_query(user_id, score_expr, group_id, tags)
            .where(
                text(
                    "document_chunks.content @@@ paradedb.match('content', :kw_query)"
                ).bindparams(kw_query=query)
            )
            .order_by(text("score DESC"))
            .limit(top_k)
        )
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
                score=float(row.score),
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

    async def get_hashes_for_document(
        self, document_id: uuid.UUID
    ) -> dict[str, list[uuid.UUID]]:
        """Maps each existing chunk's content_hash -> ALL row ids sharing that hash,
        for one document — used by Replace to decide which chunks can be reused vs
        deleted. Returning a list (not a single id) matters because two distinct
        chunk rows can legitimately share one hash: duplicate content within the
        same document (repeated boilerplate) or every legacy chunk that predates
        the content_hash backfill (all sharing hash ""). Collapsing to one id per
        hash would silently orphan the rest as undeletable zombie rows."""
        stmt = select(DocumentChunk.content_hash, DocumentChunk.id).where(
            DocumentChunk.document_id == document_id
        )
        result = await self._session.execute(stmt)
        hashes: dict[str, list[uuid.UUID]] = {}
        for row in result.all():
            hashes.setdefault(row.content_hash, []).append(row.id)
        return hashes

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
