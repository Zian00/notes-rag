import math
from unittest.mock import MagicMock

from app.core.config import Settings
from app.rag.embeddings import GeminiEmbeddingsProvider, l2_normalize


def test_l2_normalize_unit_length():
    out = l2_normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0, rel_tol=1e-6)
    assert math.isclose(out[0], 0.6, rel_tol=1e-6)


def test_l2_normalize_zero_vector_is_safe():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]


def test_embed_documents_splits_into_sub_batches_of_100(monkeypatch):
    """Gemini's batchEmbedContents rejects >100 texts in one call (400
    INVALID_ARGUMENT) — verify embed_documents splits a large document's
    chunks into multiple calls instead of sending them all at once."""
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost/db",
        jwt_secret="test-secret",
        google_api_key="fake-key",
    )
    provider = GeminiEmbeddingsProvider(settings)
    calls: list[list[str]] = []

    def fake_embed_content(model, contents, config):
        calls.append(list(contents))
        return MagicMock(embeddings=[MagicMock(values=[1.0]) for _ in contents])

    monkeypatch.setattr(provider._client.models, "embed_content", fake_embed_content)

    texts = [f"chunk-{i}" for i in range(150)]
    result = provider.embed_documents(texts)

    assert len(result) == 150
    assert [len(c) for c in calls] == [100, 50]
    # Order preserved across the split — chunk-99 is the last item of call 1.
    assert calls[0][-1] == "chunk-99"
    assert calls[1][0] == "chunk-100"
