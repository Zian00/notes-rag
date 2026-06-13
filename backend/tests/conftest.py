import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from app.core.config import get_settings
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Single source of truth for the test DB: use TEST_DATABASE_URL when set
# (e.g. CI publishes Postgres on 5432), otherwise the app's configured
# DATABASE_URL (from .env). Avoids a hard-coded port drifting from real config.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or get_settings().database_url


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
