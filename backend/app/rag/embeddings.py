import math
from abc import ABC, abstractmethod

from google import genai
from google.genai import types

from app.core.config import Settings


def l2_normalize(vector: list[float]) -> list[float]:
    """Scale to unit length. Required because Gemini only normalizes the full
    3072-dim output; truncated dims (1536) must be normalized for cosine to be valid."""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return vector
    return [x / norm for x in vector]


class EmbeddingsProvider(ABC):
    """Port: turn text into embedding vectors."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


class GeminiEmbeddingsProvider(EmbeddingsProvider):
    """Gemini embeddings via google-genai. Document and query use asymmetric task types."""

    def __init__(self, settings: Settings) -> None:
        self._client = genai.Client(api_key=settings.google_api_key)
        self._model = settings.embedding_model
        self._dim = settings.embedding_dimension
        self._doc_task = settings.embedding_doc_task_type
        self._query_task = settings.embedding_query_task_type

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        resp = self._client.models.embed_content(
            model=self._model,
            contents=texts,  # type: ignore[arg-type]
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=self._dim
            ),
        )
        embeddings = resp.embeddings or []
        return [l2_normalize(list(e.values or [])) for e in embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(texts, self._doc_task)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], self._query_task)[0]
