import hashlib
import os
from collections.abc import AsyncGenerator

import pytest
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
_TRUNCATE_TABLES = "users, refresh_tokens, documents, document_chunks, conversations"


def hash_content(content: str) -> str:
    """Helper to compute SHA-256 hash of chunk content for test fixtures."""
    return hashlib.sha256(content.encode()).hexdigest()


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
        # The test DB is built via create_all (not Alembic), so the extensions that
        # migrations 0001/0007 add to the dev DB must be enabled here too — vector
        # for the embedding column, pg_search for the BM25 index below. Idempotent.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_search"))
        await conn.run_sync(Base.metadata.create_all)
        # BM25 index isn't part of Base.metadata (SQLAlchemy has no bm25 index
        # construct) — created directly, same as migration 0007.
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS document_chunks_bm25_idx ON document_chunks "
                "USING bm25 (id, content) WITH (key_field='id')"
            )
        )
        await conn.execute(text(f"TRUNCATE {_TRUNCATE_TABLES} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session


@pytest.fixture
def fake_chat_model():
    """Function-scoped FakeChatModel with an empty responses queue.

    Tests set ``fake_chat_model.responses = [...]`` before issuing requests.
    The graph built in ``client`` holds a reference to this same object, so
    responses queued after graph construction are picked up at invocation time.
    """
    from tests.fakes import FakeChatModel

    return FakeChatModel(responses=[])


@pytest_asyncio.fixture
async def client(
    _engine: AsyncEngine, tmp_path, fake_chat_model
) -> AsyncGenerator[AsyncClient]:
    """HTTP client whose app talks to the TEST DB and uses fake OCR/embeddings + temp storage.

    Overriding the leaf provider dependencies (not the services) keeps endpoint tests fast and
    deterministic: no Google API key, no network, no tesseract binary, and uploaded files land
    in a throwaway tmp dir.

    Chat-service wiring:
    ``get_chat_service`` normally reads ``request.app.state.chat_graph``, which is set by the
    lifespan hook.  The lifespan does NOT run under ASGITransport, so ``app.state.chat_graph``
    would be unset.  We build a graph ONCE per test (backed by ``fake_chat_model`` +
    ``InMemorySaver``) and override ``get_chat_service`` to return a ``ChatService`` wrapping
    that graph.  Building the graph once (not per request) is REQUIRED so the ``InMemorySaver``
    accumulates state across requests within a test — enabling multi-turn tests.
    """
    from app.api.deps import (
        get_chat_service,
        get_embeddings,
        get_enqueue_processing,
        get_ocr,
        get_storage,
    )
    from app.core.config import get_settings
    from app.db.session import get_db
    from app.main import create_app
    from app.rag.graph import build_rag_graph
    from app.rag.storage import LocalFileStorage
    from app.services.chat import ChatService
    from langgraph.checkpoint.memory import InMemorySaver

    from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider

    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        async with maker() as session:
            yield session

    # Build the graph once so InMemorySaver state persists across requests in a test.
    settings = get_settings()
    graph = build_rag_graph(
        chat_model=fake_chat_model,
        embeddings=FakeEmbeddingsProvider(),
        sessionmaker=maker,
        settings=settings,
        checkpointer=InMemorySaver(),
    )

    # The real enqueue calls process_document.defer_async(), which needs a procrastinate
    # app opened against a real connection pool (app.open()/open_async()) — not available
    # under ASGITransport in tests. Default to a no-op so upload tests (which don't care
    # about background processing) don't hit AppNotOpen; tests that DO need chunks to
    # exist (e.g. search tests) override this again to run process() synchronously.
    async def _noop_enqueue(document_id: object) -> None:
        return None

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_embeddings] = lambda: FakeEmbeddingsProvider()
    app.dependency_overrides[get_ocr] = lambda: FakeOcrProvider()
    app.dependency_overrides[get_storage] = lambda: LocalFileStorage(str(tmp_path))
    app.dependency_overrides[get_chat_service] = lambda: ChatService(graph, maker)
    app.dependency_overrides[get_enqueue_processing] = lambda: _noop_enqueue
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Expose the underlying FastAPI app + the session maker/upload dir so individual
        # tests can add/pop their own dependency_overrides (e.g. get_enqueue_processing)
        # or build a real IngestionService against the same test DB/fakes, without a
        # second fixture.
        ac.app = app
        ac.maker = maker
        ac.upload_dir = str(tmp_path)
        yield ac


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient) -> AsyncGenerator[AsyncClient]:
    """A `client` that has registered + logged in; Authorization header preset."""
    import uuid as _uuid

    email = f"user-{_uuid.uuid4().hex}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    resp = await client.post("/auth/login", json={"email": email, "password": "password123"})
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
