import math
from unittest.mock import MagicMock

from app.core.config import Settings
from app.rag.embeddings import (
    GeminiEmbeddingsProvider,
    OpenAIEmbeddingsProvider,
    build_embeddings_provider,
    l2_normalize,
)


def _settings(**overrides):
    base = dict(database_url="postgresql+asyncpg://u:p@localhost/db", jwt_secret="test-secret")
    base.update(overrides)
    return Settings(**base)


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


def test_openai_embed_documents_returns_one_vector_per_text(monkeypatch):
    settings = _settings(openai_api_key="fake-key")
    provider = OpenAIEmbeddingsProvider(settings)
    calls: list[list[str]] = []

    def fake_create(*, input, model, dimensions):
        calls.append(list(input))
        return MagicMock(data=[MagicMock(embedding=[float(len(t))] * 1536) for t in input])

    monkeypatch.setattr(provider._client.embeddings, "create", fake_create)

    result = provider.embed_documents(["abc", "de"])

    assert len(result) == 2
    assert len(result[0]) == 1536
    assert len(calls) == 1  # small batch: single call, no splitting needed


def test_openai_embed_documents_splits_batches_over_the_limit(monkeypatch):
    """OpenAI's embeddings.create rejects arrays over its documented max size —
    verify embed_documents splits a large document's chunks across calls,
    mirroring the same safety pattern already fixed for Gemini."""
    settings = _settings(openai_api_key="fake-key")
    provider = OpenAIEmbeddingsProvider(settings)
    calls: list[list[str]] = []

    def fake_create(*, input, model, dimensions):
        calls.append(list(input))
        return MagicMock(data=[MagicMock(embedding=[1.0] * 1536) for _ in input])

    monkeypatch.setattr(provider._client.embeddings, "create", fake_create)

    texts = [f"chunk-{i}" for i in range(2100)]
    result = provider.embed_documents(texts)

    assert len(result) == 2100
    assert len(calls) == 2  # 2048 + 52, given a 2048-item max batch size
    assert sum(len(c) for c in calls) == 2100


def test_openai_embed_query_returns_single_vector(monkeypatch):
    settings = _settings(openai_api_key="fake-key")
    provider = OpenAIEmbeddingsProvider(settings)

    def fake_create(*, input, model, dimensions):
        return MagicMock(data=[MagicMock(embedding=[0.5] * 1536)])

    monkeypatch.setattr(provider._client.embeddings, "create", fake_create)

    vector = provider.embed_query("what is photosynthesis?")

    assert len(vector) == 1536


def test_build_embeddings_provider_returns_google_provider():
    settings = _settings(embedding_provider="google", google_api_key="fake-key")
    assert isinstance(build_embeddings_provider(settings), GeminiEmbeddingsProvider)


def test_build_embeddings_provider_returns_openai_provider():
    settings = _settings(embedding_provider="openai", openai_api_key="fake-key")
    assert isinstance(build_embeddings_provider(settings), OpenAIEmbeddingsProvider)
