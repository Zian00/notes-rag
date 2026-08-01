import pytest
from app.core.config import Settings
from app.rag.llm import build_chat_model


def _settings(**over):
    base = {"database_url": "postgresql+asyncpg://u:p@h:5432/db", "jwt_secret": "x"}
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def test_google_provider(monkeypatch):
    captured = {}

    class FakeChat:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("app.rag.llm.ChatGoogleGenerativeAI", FakeChat)
    model = build_chat_model(_settings(google_api_key="k", llm_model="gemini-2.5-flash"))
    assert isinstance(model, FakeChat)
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["temperature"] == 0.2


def test_openai_provider(monkeypatch):
    captured = {}

    class FakeChat:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("app.rag.llm.ChatOpenAI", FakeChat)
    model = build_chat_model(
        _settings(llm_provider="openai", openai_api_key="k", llm_model="gpt-4o-mini")
    )
    assert isinstance(model, FakeChat)
    assert captured["model"] == "gpt-4o-mini"
    assert captured["temperature"] == 0.2
    assert captured["api_key"] == "k"


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        build_chat_model(_settings(llm_provider="mystery"))  # type: ignore[arg-type]


def test_openai_compatible_requires_base_url():
    with pytest.raises(ValueError):
        build_chat_model(_settings(llm_provider="openai_compatible", llm_base_url=None))
