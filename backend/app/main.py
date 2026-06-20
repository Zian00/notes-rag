from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api import auth, chat, conversations, documents, health, search
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine, get_sessionmaker
from app.rag.embeddings import GeminiEmbeddingsProvider
from app.rag.graph import build_rag_graph
from app.rag.llm import build_chat_model


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    settings = get_settings()

    # --- psycopg3 connection pool for the LangGraph checkpointer ---
    # AsyncPostgresSaver requires: autocommit=True (it manages its own transactions),
    # prepare_threshold=0 (disables server-side prepared statements — needed because
    # the pool reuses connections and prepared statements don't survive reconnects),
    # row_factory=dict_row (the saver parses rows as dicts, not tuples).
    pool = AsyncConnectionPool(
        conninfo=settings.checkpointer_conninfo,
        max_size=10,
        open=False,  # open=False + explicit await pool.open() avoids the deprecation warning
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()

    # psycopg_pool's generic type doesn't reflect row_factory at the type level,
    # so we suppress the arg-type mismatch — at runtime the pool IS dict-row typed.
    checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    # setup() is idempotent: creates the checkpoint tables if they don't exist yet.
    await checkpointer.setup()

    chat_model = build_chat_model(settings)
    # Use the same GeminiEmbeddingsProvider constructor as get_embeddings() in deps.py.
    embeddings = GeminiEmbeddingsProvider(settings)
    _app.state.chat_graph = build_rag_graph(
        chat_model, embeddings, get_sessionmaker(), settings, checkpointer
    )

    try:
        yield
    finally:
        # Shutdown: close the psycopg pool first, then dispose the SQLAlchemy engine.
        await pool.close()
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
    app.include_router(documents.router)
    app.include_router(search.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
