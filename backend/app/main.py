from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api import auth, health
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    """Application factory (function by deliberate choice; see spec)."""
    settings = get_settings()
    app = FastAPI(title="Notes RAG API", version="0.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
