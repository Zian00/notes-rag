"""Configurable chat-LLM factory.

Returns a LangChain ``BaseChatModel`` selected by ``settings.llm_provider``. We use a
factory (not a hand-rolled ABC) because LangGraph's tool-calling + token streaming
integrate with ``BaseChatModel`` (``.bind_tools``, ``.astream``); re-implementing that
behind a custom interface would be costly and fragile. Swapping Gemini → Claude →
local (Ollama/vLLM via an OpenAI-compatible endpoint) is config-only; the graph never
names a provider.
"""

from langchain_core.language_models import BaseChatModel

# Imported at module top so tests can monkeypatch the symbol. Only the google provider
# is a hard dependency in Phase 3; anthropic/openai packages are add-when-switching and
# imported lazily inside the factory so their absence doesn't break import.
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import Settings


def build_chat_model(settings: Settings) -> BaseChatModel:
    provider = settings.llm_provider
    if provider == "google":
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            google_api_key=settings.google_api_key or None,
        )
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
        except ImportError as exc:  # add-when-switching dependency
            raise ValueError(
                "llm_provider='anthropic' needs `uv add langchain-anthropic`"
            ) from exc
        return ChatAnthropic(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            api_key=settings.anthropic_api_key or None,
        )
    if provider == "openai_compatible":
        if not settings.llm_base_url:
            raise ValueError("llm_provider='openai_compatible' requires llm_base_url")
        try:
            from langchain_openai import ChatOpenAI  # noqa: PLC0415
        except ImportError as exc:
            raise ValueError(
                "llm_provider='openai_compatible' needs `uv add langchain-openai`"
            ) from exc
        return ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            base_url=settings.llm_base_url,
            api_key="not-needed",  # local servers ignore the key
        )
    raise ValueError(f"Unknown llm_provider: {provider!r}")
