from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, created lazily on first use.

    Lazy creation means importing this module does not open a connection pool
    or bind to an event loop — the engine is built inside the running loop on
    the first request (and reused thereafter).
    """
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=False, future=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory, bound to the lazy engine."""
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding an async DB session."""
    async with get_sessionmaker()() as session:
        yield session
