import uuid
from typing import Protocol

from app.db.repositories.chunk import ChunkRepository, ChunkSearchResult
from app.rag.embeddings import EmbeddingsProvider


class _Reranker(Protocol):
    def rerank(self, query: str, chunks: list[ChunkSearchResult]) -> list[ChunkSearchResult]: ...


class RetrievalService:
    """Embeds a query and returns the user's most similar chunks.

    When a reranker is provided the service retrieves a larger candidate pool
    from pgvector (candidate_k), re-scores with the cross-encoder, and trims
    to the final top_k. Without a reranker it fetches top_k directly — no
    extra DB work.
    """

    def __init__(
        self,
        chunks: ChunkRepository,
        embeddings: EmbeddingsProvider,
        default_top_k: int,
        *,
        # Default matches retrieval_candidate_k in config.py; production wiring
        # always passes settings.retrieval_candidate_k explicitly via deps.py.
        candidate_k: int = 20,
        reranker: _Reranker | None = None,
    ) -> None:
        self._chunks = chunks
        self._embeddings = embeddings
        self._default_top_k = default_top_k
        self._candidate_k = candidate_k
        self._reranker = reranker

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
        effective_top_k = top_k or self._default_top_k
        # With reranker: over-fetch (candidate_k) so the cross-encoder sees more
        # context before trimming. Without reranker: fetch exactly what we need.
        fetch_k = self._candidate_k if self._reranker is not None else effective_top_k
        embedding = self._embeddings.embed_query(query)
        candidates = await self._chunks.search_similar(
            user_id,
            embedding,
            top_k=fetch_k,
            course=course,
            tags=tags,
        )
        if self._reranker is None:
            return candidates
        reranked = self._reranker.rerank(query, candidates)
        return reranked[:effective_top_k]
