import math
from abc import ABC, abstractmethod

from google import genai
from google.genai import types

from app.core.config import Settings

# Gemini's batchEmbedContents endpoint rejects any call carrying more than this
# many texts (400 INVALID_ARGUMENT) — embed_documents splits into sub-batches.
_MAX_BATCH_SIZE = 100


def l2_normalize(vector: list[float]) -> list[float]:
    """Scale to unit length. Required because Gemini only normalizes the full
    3072-dim output; truncated dims (1536) must be normalized for cosine to be valid."""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return vector
    return [x / norm for x in vector]


class EmbeddingsProvider(ABC):
    """Port: turn text into embedding vectors.

    An "embedding" is a list of numbers that captures the *meaning* of text — similar
    meanings produce nearby vectors, which is what makes semantic search work. This is
    an abstract interface (a "port"); a concrete adapter implements it, and code depends
    on this interface, not the vendor.
    """

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...  # batch (chunks)

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...  # single (search query)


class GeminiEmbeddingsProvider(EmbeddingsProvider):
    """Gemini embeddings via google-genai. Document and query use asymmetric task types."""

    def __init__(self, settings: Settings) -> None:
        self._client = genai.Client(api_key=settings.google_api_key)
        self._model = settings.embedding_model
        self._dim = settings.embedding_dimension
        # Asymmetric task types: documents and queries are embedded with different hints
        # (RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY), which measurably improves retrieval.
        self._doc_task = settings.embedding_doc_task_type
        self._query_task = settings.embedding_query_task_type

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        resp = self._client.models.embed_content(
            model=self._model,
            contents=texts,  # type: ignore[arg-type]
            # output_dimensionality truncates the model's native 3072 dims down to ours (1536).
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=self._dim
            ),
        )
        embeddings = resp.embeddings or []
        # Re-normalize: Gemini only unit-normalizes the full 3072-dim output, so truncated
        # vectors must be normalized by us for cosine distance to be valid.
        return [l2_normalize(list(e.values or [])) for e in embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []  # the API rejects an empty batch; nothing to embed anyway
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), _MAX_BATCH_SIZE):
            embeddings.extend(self._embed(texts[i : i + _MAX_BATCH_SIZE], self._doc_task))
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], self._query_task)[0]  # one text in → one vector out
