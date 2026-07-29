from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.db.repositories.chunk import ChunkSearchResult


class Reranker:
    """Re-scores retrieved chunks with a fastembed ONNX cross-encoder.

    Cross-encoders see (query, chunk) pairs jointly — far more accurate than
    the embedding cosine similarity used for the initial retrieval, but too
    slow to run against the whole corpus. The pattern is: retrieve a larger
    candidate pool from pgvector (cheap), then rerank with the cross-encoder
    (slower but bounded), then return the top-k results.

    Load this exactly once per process via deps.get_reranker() (@lru_cache).
    """

    _MODEL = "BAAI/bge-reranker-base"

    def __init__(self) -> None:
        self._encoder: TextCrossEncoder = TextCrossEncoder(self._MODEL)

    def rerank(self, query: str, chunks: list[ChunkSearchResult]) -> list[ChunkSearchResult]:
        """Return chunks sorted by cross-encoder relevance score, highest first."""
        if not chunks:
            return []
        scores = list(self._encoder.rerank(query, [c.content for c in chunks]))
        return [c for _, c in sorted(zip(scores, chunks), key=lambda p: p[0], reverse=True)]
