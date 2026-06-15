from app.rag.embeddings import EmbeddingsProvider
from app.rag.ocr import OcrProvider
from PIL import Image


class FakeOcrProvider(OcrProvider):
    """Returns a fixed string, ignoring the image — deterministic, no Tesseract."""

    def __init__(self, text: str = "ocr text") -> None:
        self._text = text

    def extract_text(self, image: Image.Image) -> str:
        return self._text


class FakeEmbeddingsProvider(EmbeddingsProvider):
    """Deterministic unit vectors derived from text length — no network/key.

    Vector i is a one-hot at position (len(text) % dim), so different-length texts
    sort deterministically by cosine distance.
    """

    def __init__(self, dimension: int = 1536) -> None:
        self._dim = dimension

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self._dim
        v[len(text) % self._dim] = 1.0
        return v

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
