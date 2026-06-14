from app.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@localhost:5432/db"
    assert settings.jwt_secret == "test-secret"
    assert settings.cors_origins == ["http://localhost:5173"]
    assert settings.environment == "development"  # default


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    settings = Settings()

    assert settings.cors_origins == ["http://localhost:5173"]


def test_settings_auth_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5433/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    settings = Settings()

    assert settings.jwt_algorithm == "HS256"
    assert settings.access_token_expire_minutes == 15
    assert settings.refresh_token_expire_days == 7
    assert settings.cookie_secure is False
    assert settings.cookie_samesite == "lax"
