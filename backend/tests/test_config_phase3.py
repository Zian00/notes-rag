from app.core.config import Settings


def _settings(**over: object) -> Settings:
    base = {"database_url": "postgresql+asyncpg://u:p@localhost:5432/db", "jwt_secret": "x"}
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def test_phase3_defaults() -> None:
    s = _settings()
    assert s.llm_provider == "google"
    assert s.llm_model == "gemini-2.5-flash"
    assert s.llm_temperature == 0.2
    assert s.llm_base_url is None
    assert s.agentic_retrieval is True
    assert s.max_grade_retries == 2
    assert s.chat_history_limit == 20


def test_checkpointer_conninfo_strips_asyncpg() -> None:
    s = _settings()
    # psycopg3 needs a driver-less URL; asyncpg's "+asyncpg" must be removed.
    assert s.checkpointer_conninfo == "postgresql://u:p@localhost:5432/db"
