import uuid

from app.db.repositories.chunk import ChunkRepository, ChunkSearchResult
from app.rag.embeddings import EmbeddingsProvider


class RetrievalService:
    """Embeds a query and returns the user's most similar chunks."""

    def __init__(
        self,
        chunks: ChunkRepository,
        embeddings: EmbeddingsProvider,
        default_top_k: int,
    ) -> None:
        self._chunks = chunks
        self._embeddings = embeddings
        self._default_top_k = default_top_k

    async def search(
        self,
        user_id: uuid.UUID,
        query: str,
        top_k: int | None = None,
        course: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ChunkSearchResult]:
        if not query.strip():
            return []  # nothing to embed; avoid a pointless API call
        # Two steps: turn the query into a vector, then find the nearest stored chunks.
        embedding = self._embeddings.embed_query(query)
        return await self._chunks.search_similar(
            user_id,
            embedding,
            top_k=top_k or self._default_top_k,  # caller override, else the configured default
            course=course,
            tags=tags,
        )
