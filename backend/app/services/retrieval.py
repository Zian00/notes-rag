import uuid
from typing import Protocol

from app.db.repositories.chunk import ChunkRepository, ChunkSearchResult
from app.rag.embeddings import EmbeddingsProvider


class _Reranker(Protocol):
    def rerank(self, query: str, chunks: list[ChunkSearchResult]) -> list[ChunkSearchResult]: ...


def _merge_dedupe(
    vector_candidates: list[ChunkSearchResult], keyword_candidates: list[ChunkSearchResult]
) -> list[ChunkSearchResult]:
    """Concatenate both candidate lists, keeping only the first occurrence of each
    chunk_id. No fusion scoring — a downstream reranker re-scores every candidate
    against the actual query text regardless of which retrieval path found it, so
    combining RRF/weighted scores here would be redundant work (see hybrid-search
    design)."""
    seen: set[uuid.UUID] = set()
    merged: list[ChunkSearchResult] = []
    for chunk in vector_candidates + keyword_candidates:
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            merged.append(chunk)
    return merged


class RetrievalService:
    """Embeds a query and returns the user's most similar chunks.

    Three independent knobs compose: candidate_k over-fetches before trimming to
    top_k, keyword_search adds a BM25 pass merged with the vector results, and
    reranker re-scores whatever candidates were gathered. Either reranker or
    keyword_search triggers over-fetching so the wider pool is available before
    final scoring/trimming.

    keyword_search without a reranker has no defined relevance order across the
    two candidate lists (vector hits sort first, purely by list position) —
    production always pairs keyword_search with a reranker (see deps.py), so this
    combination only arises in tests.
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
        keyword_search: bool = False,
    ) -> None:
        self._chunks = chunks
        self._embeddings = embeddings
        self._default_top_k = default_top_k
        self._candidate_k = candidate_k
        self._reranker = reranker
        self._keyword_search = keyword_search

    async def search(
        self,
        user_id: uuid.UUID,
        query: str,
        top_k: int | None = None,
        group_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
    ) -> list[ChunkSearchResult]:
        if not query.strip():
            return []  # nothing to embed; avoid a pointless API call
        effective_top_k = top_k or self._default_top_k
        # Over-fetch (candidate_k) whenever something downstream needs a wider pool
        # to work with — reranking or merging in a second (keyword) retrieval path.
        use_candidate_pool = self._reranker is not None or self._keyword_search
        fetch_k = self._candidate_k if use_candidate_pool else effective_top_k
        embedding = self._embeddings.embed_query(query)
        candidates = await self._chunks.search_similar(
            user_id,
            embedding,
            top_k=fetch_k,
            group_id=group_id,
            tags=tags,
        )
        if self._keyword_search:
            keyword_candidates = await self._chunks.search_keyword(
                user_id, query, top_k=fetch_k, group_id=group_id, tags=tags
            )
            candidates = _merge_dedupe(candidates, keyword_candidates)
        if self._reranker is not None:
            candidates = self._reranker.rerank(query, candidates)
        return candidates[:effective_top_k]
