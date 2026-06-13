# Foundation: Key Concepts in the Backend Rebuild

This document explains the design decisions and patterns introduced in Phase 0 of the Notes RAG rebuild. If you are new to any of the libraries or patterns used, read this alongside the code. Each section answers two questions: what is this thing, and why did we choose it here.

---

## App Factory (`create_app()`)

An **app factory** is a function that constructs and returns a configured application object rather than creating the app at module import time. In `backend/app/main.py`, `create_app()` builds the FastAPI instance, attaches middleware, and registers routers before returning it. The module then calls `app = create_app()` at the bottom so that uvicorn (the ASGI server) can import `app.main:app` and run it.

The benefit is testability. When your test file imports `create_app`, it can call it directly to spin up a fresh, isolated app instance without touching the global one. This is exactly what `conftest.py` does — each test run constructs its own app via `create_app()` and passes it to an `AsyncClient`. Without a factory, tests would share state with the production singleton, which leads to subtle ordering bugs.

---

## pydantic-settings (`Settings` and `get_settings()`)

`pydantic-settings` is a library that lets you declare your configuration as a typed Python class. In `backend/app/core/config.py`, `Settings` inherits from `BaseSettings` and declares fields like `database_url: str` and `jwt_secret: str`. When `Settings()` is instantiated, it automatically reads values from environment variables and, if present, from a `.env` file.

The alternative — sprinkling `os.getenv("DATABASE_URL")` calls throughout the codebase — has two problems: you get `str | None` everywhere (no type safety), and there is no single place to validate that required values are present at startup. With `Settings`, a missing `database_url` raises a validation error immediately when the app boots, not silently at runtime when a request arrives.

`get_settings()` is decorated with `@lru_cache`. Without this, every call to `get_settings()` would re-parse the environment and re-read the `.env` file. The cache ensures the `Settings` object is constructed once per process and reused everywhere — which also makes it easy to override in tests by clearing the cache.

---

## Async SQLAlchemy (engine, sessionmaker, session)

SQLAlchemy is the Python ORM we use to talk to Postgres. The **async** variant (`sqlalchemy.ext.asyncio`) lets database calls be non-blocking so that FastAPI can handle other requests while waiting for Postgres to respond.

Three objects matter here:

- **Engine** (`create_async_engine`): Manages the underlying connection pool. There should be one engine per process, created at startup in `session.py`.
- **Sessionmaker** (`async_sessionmaker`): A factory that stamps out new `AsyncSession` instances. We configure it once with `expire_on_commit=False` (explained below) and reuse it for every request.
- **Session** (`AsyncSession`): The unit of work. Each HTTP request gets its own session. It tracks which ORM objects have been loaded, buffers writes, and commits or rolls back as a transaction.

`get_db()` is a FastAPI dependency that yields one session per request:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
```

The `async with` block ensures the session is always closed (and any uncommitted changes rolled back) when the request ends — whether it succeeds or raises an exception.

`expire_on_commit=False` means that after you call `await session.commit()`, the ORM objects you already hold remain readable without triggering another database round-trip. The default behaviour expires (invalidates) objects on commit, which would cause a `MissingGreenlet` error in async code if you tried to access an attribute after the commit.

---

## Alembic (database migrations)

A **migration** is a versioned script that describes a change to the database schema — adding a table, dropping a column, enabling an extension. Alembic is the migration tool for SQLAlchemy projects.

In this project, migrations live under `backend/app/db/migrations/versions/`. Each file has an `upgrade()` function (apply the change) and a `downgrade()` function (reverse it). Alembic tracks which migrations have been applied by writing their revision IDs to an `alembic_version` table in the database.

Alembic has two runtime modes:

- **Offline mode**: Generates SQL without connecting to a database. Useful for auditing or deploying to a DBA. Called via `alembic upgrade head --sql`.
- **Online mode**: Connects to the database and runs migrations directly. This is what `make migrate` does.

The migration `env.py` injects the database URL from `Settings` at runtime:

```python
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```

This means we never hard-code a connection string in version-controlled files. The URL comes from the environment (or `.env`) at the time `alembic upgrade head` is run.

The first migration (`0001_enable_vector`) runs `CREATE EXTENSION IF NOT EXISTS vector`. This must execute before any table tries to use the `vector` column type — hence it is revision zero with no predecessor.

---

## pgvector

**pgvector** is a Postgres extension that adds a native `vector` data type and vector similarity operators (cosine distance, L2 distance, inner product). Once the extension is enabled, you can store embedding vectors as a regular column alongside text and timestamps in the same table.

We use pgvector rather than a standalone vector database (like Chroma or Pinecone) because it keeps everything in one place. Your document content, metadata, and vector embeddings all live in the same Postgres instance. LangGraph (used in Phase 3) also stores its agent checkpoints in Postgres. A single database means simpler operations, consistent transactions, and one fewer service to manage and back up.

---

## OOP Layering: Boundary / Control / Entity

The backend is structured in three layers, loosely following layered architecture with hexagonal (ports and adapters) influences:

- **Boundary (`api/`)**: HTTP handlers. They parse the incoming request, call a service, and format the response. No business logic lives here.
- **Control (`services/`)**: Business logic. Services enforce rules ("the embedding must be non-empty"), orchestrate multi-step operations, and call repositories. They are unaware of HTTP — a service method takes plain Python objects and returns plain Python objects.
- **Entity (`db/` + repositories)**: Persistence. Repositories implement CRUD against the database. They know about ORM models and SQL, but not about HTTP or business rules.

`BaseRepository` in `backend/app/db/repositories/base.py` is a generic class parameterised by the model type (`ModelT`). It provides `create`, `get`, `list`, and `delete` out of the box. A concrete repository (e.g. `DocumentRepository`) inherits from it and adds domain-specific queries.

Dependency injection via FastAPI's `Depends` wires everything together at request time. A handler declares `db: AsyncSession = Depends(get_db)`, receives a session, constructs a repository from it, constructs a service from the repository, and calls the service. This keeps each layer testable in isolation — you can inject a fake session, a fake repository, or a fake service depending on what you want to test.

The **ABC ports/adapters** pattern (abstract base classes defining contracts that concrete adapters implement) is not yet used in Phase 0 but is the planned structure for later phases where we swap embedding providers or storage backends without touching business logic.

---

## pytest Fixtures

Fixtures in pytest are reusable setup/teardown helpers declared with `@pytest_asyncio.fixture` (for async fixtures) or `@pytest.fixture`.

The project has two core fixtures in `conftest.py`:

- **`db_session`**: Creates a fresh async engine pointing at the test database, yields an `AsyncSession`, then disposes the engine. Used by integration tests that need a real database connection.
- **`client`**: Calls `create_app()` to get a fresh FastAPI app instance, wraps it in an `ASGITransport`, and yields an `AsyncClient`. Used by tests that hit HTTP endpoints. Because it uses `ASGITransport`, no actual network socket is opened — requests are dispatched in-process.

The distinction between unit and integration tests matters here:

- `test_health.py` constructs a `_FakeSession` that simulates success or database failure without touching Postgres at all. This is a **unit test** — fast, no external dependencies.
- `test_repository.py` uses the real `db_session` fixture and creates an actual `_test_widgets` table in the test database. This is an **integration test** — slower, but it validates that the SQL actually works.

The `widget_table` fixture in `test_repository.py` calls `await db_session.rollback()` before dropping the table. This is necessary because if the test's session still holds an open transaction with a lock on the table, the `DROP TABLE` command will deadlock. Rolling back first releases those locks cleanly.

---

## uv and Make

**uv** is a fast Python package and environment manager (written in Rust) that replaces pip + venv for this project. It reads `pyproject.toml` for dependencies and writes a `uv.lock` file that pins exact versions for reproducible installs. Key commands:

- `uv sync` — create/update the virtual environment from the lock file.
- `uv sync --frozen` — the same but fails if the lock file is out of date (used in CI to catch drift).
- `uv run <cmd>` — run a command inside the project's virtual environment without needing to activate it first.

The **Makefile** at the repository root is the task runner. It wraps long `uv run` and `docker compose` commands behind short names so you only need to remember `make dev`, `make test`, `make migrate`, and so on. All backend targets prefix their commands with `cd backend &&` so they run in the right working directory.

---

## Docker Compose and the 5433 Port Mapping

The `docker-compose.yml` at the repository root defines two services:

- **postgres**: The `pgvector/pgvector:pg16` image, which ships Postgres 16 with the pgvector extension pre-installed. Its container-internal port is 5432.
- **backend**: The FastAPI application built from `backend/Dockerfile`.

The Postgres service maps host port `5433` to container port `5432` (`"5433:5432"`). This is deliberate: many developers have a native Postgres installation that already occupies port 5432 on the host. Using 5433 avoids a port conflict so the Docker stack starts cleanly even with a local Postgres running.

The backend container connects to Postgres using the Docker internal network (`postgres:5432`), not the host mapping, so it always uses 5432 internally.

In CI (GitHub Actions), there is no native Postgres, so the service maps `5432:5432` directly and `DATABASE_URL` points at `localhost:5432`. The 5433 offset is a local-dev convenience only and is not replicated in CI.
