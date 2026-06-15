import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from app.core.config import get_settings
from app.db.base import Base
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _test_database_url() -> str:
    """Resolve the test DB URL.

    Use TEST_DATABASE_URL when set (e.g. CI points it at its own Postgres).
    Otherwise derive a dedicated `<dbname>_test` database from DATABASE_URL so
    tests NEVER touch dev/real data.
    """
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        return explicit
    base, _, dbname = get_settings().database_url.rpartition("/")
    return f"{base}/{dbname}_test"


TEST_DATABASE_URL = _test_database_url()

# Tables whose rows are wiped between tests (NOT alembic_version).
_TRUNCATE_TABLES = "users, refresh_tokens, documents, document_chunks"


@pytest_asyncio.fixture
async def _engine() -> AsyncGenerator[AsyncEngine]:
    """Per-test engine on the dedicated test DB.

    A fresh engine per test avoids asyncpg event-loop binding issues (each test
    gets its own loop). On setup we ensure the schema exists (create_all) and
    truncate the auth tables so every test starts from a clean slate.
    """
    import app.models  # noqa: F401  (register models on Base.metadata)

    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with engine.begin() as conn:
        # The test DB is built via create_all (not Alembic), so the pgvector extension
        # that migration 0001 adds to the dev DB must be enabled here too — the
        # document_chunks.embedding Vector column needs it. Idempotent + non-destructive.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(f"TRUNCATE {_TRUNCATE_TABLES} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(_engine: AsyncEngine) -> AsyncGenerator[AsyncClient]:
    """HTTP client whose app talks to the TEST DB via a get_db override."""
    from app.db.session import get_db
    from app.main import create_app

    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
