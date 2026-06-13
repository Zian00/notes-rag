# Phase 0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **COMMIT POLICY (user override):** NEVER run `git commit` automatically. The "Commit" step in each task is a checkpoint — pause and let the user run/approve the commit. Prepare the staged change and show the suggested message, then wait.

**Goal:** Stand up a running, tested, feature-free FastAPI + Postgres(pgvector) backend skeleton with OOP layered/hexagonal structure, async SQLAlchemy + Alembic, uv tooling, and minimal CI.

**Architecture:** Layered (Boundary `api/` → Control `services/` → Entity `db/`) with hexagonal ports/adapters reserved for later phases. Async throughout. Business logic in service classes; CRUD behind repository classes; config in one typed `Settings` object; DI via FastAPI `Depends`.

**Tech Stack:** Python 3.13, uv, FastAPI, Uvicorn, SQLAlchemy 2.0 (async) + asyncpg, Alembic, pydantic-settings, pgvector (`pgvector/pgvector:pg16`), pytest + pytest-asyncio + httpx, ruff, mypy, GNU Make, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-13-foundation-design.md`

---

## File Structure (created in this phase)

| Path | Responsibility |
|------|----------------|
| `legacy/` | The old Streamlit app (`app.py`, `core/`, `utils/`, `requirements.txt`), moved for reference |
| `backend/pyproject.toml` | Project metadata, deps, ruff/mypy/pytest config |
| `backend/app/__init__.py` | Package marker |
| `backend/app/main.py` | `create_app()` factory; CORS; router registration; lifespan |
| `backend/app/core/config.py` | `Settings` (pydantic-settings) + cached `get_settings()` |
| `backend/app/core/logging.py` | `configure_logging()` structured logging |
| `backend/app/db/base.py` | SQLAlchemy `DeclarativeBase` subclass |
| `backend/app/db/session.py` | Async engine, sessionmaker, `get_db()` dependency |
| `backend/app/db/repositories/base.py` | Generic `BaseRepository` (CRUD) |
| `backend/app/services/health.py` | `HealthService.check()` business logic |
| `backend/app/schemas/health.py` | `HealthResponse` pydantic model |
| `backend/app/api/health.py` | `GET /health` router (handler) |
| `backend/app/db/migrations/` | Alembic env + versions (enable `vector` extension) |
| `backend/alembic.ini` | Alembic config |
| `backend/tests/conftest.py` | Test fixtures (settings, engine, session, async client) |
| `backend/tests/test_config.py` | Settings unit test |
| `backend/tests/test_repository.py` | BaseRepository integration test |
| `backend/tests/test_health.py` | Health service unit + endpoint integration test |
| `backend/Dockerfile` | uv-based backend image |
| `backend/.env.example` | Placeholder env vars |
| `Makefile` | Repo-root task runner (up/down/dev/test/lint/typecheck/migrate/…) |
| `docker-compose.yml` | `postgres` (pgvector) + `backend` services |
| `.github/workflows/ci.yml` | ruff + mypy + pytest on push |
| `.devcontainer/devcontainer.json` | Retargeted away from Streamlit |
| `docs/learning/00-foundation.md` | Concept explainer |
| `README.md` | Updated for new stack (modify) |
| `.gitignore` | Add backend/legacy entries (modify) |

---

## Prerequisites (one-time, manual)

- [x] **Install uv** — DONE (`uv 0.11.21`).

- [ ] **Install make** (not currently installed).

Run: `winget install ezwinports.make`
Then restart the shell and verify: `make --version`
Expected: prints GNU Make version info.

- [ ] **(Optional, user) Rotate `GOOGLE_API_KEY`** in Google Cloud Console. Not blocking Phase 0; the key is unused until Phase 3. New key goes only in the local gitignored `backend/.env`.

---

## Task 1: Move the legacy Streamlit app aside

**Files:**
- Move: `app.py`, `core/`, `utils/`, `requirements.txt` → `legacy/`
- Modify: `.gitignore`

- [ ] **Step 1: Create `legacy/` and move the old app**

Run (from repo root):
```bash
mkdir -p legacy
git mv app.py legacy/app.py
git mv core legacy/core
git mv utils legacy/utils
git mv requirements.txt legacy/requirements.txt
```
(If `git mv` complains about untracked files, use plain `mv` instead.)

- [ ] **Step 2: Move the existing chroma_db out of the way (it is gitignored, so plain move)**

Run: `mv chroma_db legacy/chroma_db 2>/dev/null || echo "no chroma_db to move"`

- [ ] **Step 3: Update `.gitignore`** — replace the whole file with:

```gitignore
# Vector database (legacy)
legacy/chroma_db/

# Environment variables
.env
*.env
!*.env.example

# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.so
.mypy_cache/
.pytest_cache/
.ruff_cache/

# Virtual environment
.venv/
venv/
env/

# uv
# (uv.lock IS committed; do not ignore it)

# IDE
.vscode/

# OS
.DS_Store
```

- [ ] **Step 4: Verify structure**

Run: `ls legacy && echo "---" && ls`
Expected: `legacy/` contains `app.py core utils requirements.txt`; repo root no longer has them.

- [ ] **Step 5: Commit (await user approval)**

```bash
git add -A
git commit -m "chore: move legacy Streamlit app to legacy/"
```

---

## Task 2: Initialize the backend project with uv

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/README.md` (stub), `backend/.python-version`

- [ ] **Step 1: Create the backend package skeleton**

Run (from repo root):
```bash
mkdir -p backend/app/api backend/app/core backend/app/db/repositories backend/app/services backend/app/schemas backend/tests
touch backend/app/__init__.py backend/app/api/__init__.py backend/app/core/__init__.py backend/app/db/__init__.py backend/app/db/repositories/__init__.py backend/app/services/__init__.py backend/app/schemas/__init__.py backend/tests/__init__.py
```

- [ ] **Step 2: Create `backend/pyproject.toml`**

```toml
[project]
name = "notes-rag-backend"
version = "0.0.0"
description = "Agentic RAG backend for lecture notes (FastAPI + LangGraph)"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic-settings>=2.6",
    "pgvector>=0.3.6",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.8",
    "mypy>=1.13",
]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["app", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]

[tool.mypy]
python_version = "3.12"
plugins = ["pydantic.mypy"]
warn_unused_ignores = true
disallow_untyped_defs = true
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v"
```

- [ ] **Step 3: Pin the Python version**

Create `backend/.python-version` containing exactly:
```
3.13
```

- [ ] **Step 4: Resolve and install dependencies**

Run (from `backend/`): `uv sync`
Expected: creates `backend/.venv` and `backend/uv.lock`; installs all deps without error.

- [ ] **Step 5: Verify the toolchain runs**

Run (from `backend/`): `uv run ruff --version && uv run mypy --version && uv run pytest --version`
Expected: all three print versions.

- [ ] **Step 6: Create the root `Makefile`** (repo root, NOT `backend/`). Make is tab-indented — recipe lines MUST start with a real TAB, not spaces.

```makefile
.PHONY: up down db dev test lint format typecheck migrate revision check

# --- Docker ---
up:          ## Build + start the full stack
	docker compose up --build -d
down:        ## Stop the stack
	docker compose down
db:          ## Start only Postgres
	docker compose up -d postgres

# --- Backend (via uv) ---
dev:         ## Run the API with autoreload
	cd backend && uv run uvicorn app.main:app --reload --port 8000
test:        ## Run tests
	cd backend && uv run pytest
lint:        ## Lint
	cd backend && uv run ruff check .
format:      ## Auto-format
	cd backend && uv run ruff format .
typecheck:   ## Type-check
	cd backend && uv run mypy app
migrate:     ## Apply DB migrations
	cd backend && uv run alembic upgrade head
revision:    ## Create a migration: make revision m="message"
	cd backend && uv run alembic revision --autogenerate -m "$(m)"
check: lint typecheck test  ## Lint + type-check + test
```

- [ ] **Step 7: Verify make works**

Run (from repo root): `make --version` then `make lint` (expect ruff to run; it may report no files yet — that's fine).

- [ ] **Step 8: Commit (await user approval)**

```bash
git add backend/pyproject.toml backend/uv.lock backend/.python-version backend/app backend/tests Makefile
git commit -m "chore: initialize backend project with uv + Makefile"
```

---

## Task 3: Typed configuration (Settings)

**Files:**
- Create: `backend/app/core/config.py`, `backend/.env.example`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_config.py`

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.config'`.

- [ ] **Step 3: Implement `backend/app/core/config.py`**

```python
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str

    # Auth (used from Phase 1)
    jwt_secret: str

    # LLM (used from Phase 3)
    google_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"

    # App
    environment: Literal["development", "production"] = "development"
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (one per process)."""
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `backend/`): `uv run pytest tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Create `backend/.env.example`**

```dotenv
# Copy to backend/.env and fill in. NEVER commit the real .env.
DATABASE_URL=postgresql+asyncpg://notes:notes@localhost:5432/notes_rag
JWT_SECRET=change-me-to-a-long-random-string
GOOGLE_API_KEY=your-google-api-key-here
LLM_MODEL=gemini-2.5-flash
ENVIRONMENT=development
CORS_ORIGINS=["http://localhost:5173"]
```

- [ ] **Step 6: Create local `backend/.env`** (gitignored) by copying the example so later tasks can run locally.

Run (from `backend/`): `cp .env.example .env`

- [ ] **Step 7: Commit (await user approval)**

```bash
git add backend/app/core/config.py backend/tests/test_config.py backend/.env.example
git commit -m "feat: add typed Settings config"
```

---

## Task 4: Structured logging

**Files:**
- Create: `backend/app/core/logging.py`
- Test: `backend/tests/test_logging.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_logging.py`

```python
import logging

from app.core.logging import configure_logging


def test_configure_logging_sets_level():
    configure_logging(level="INFO")
    assert logging.getLogger("app").level == logging.INFO


def test_configure_logging_is_idempotent():
    configure_logging(level="INFO")
    configure_logging(level="DEBUG")
    logger = logging.getLogger("app")
    # No duplicate handlers on repeat calls.
    assert len(logger.handlers) == 1
    assert logger.level == logging.DEBUG
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `uv run pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.logging'`.

- [ ] **Step 3: Implement `backend/app/core/logging.py`**

```python
import logging
import sys

_LOGGER_NAME = "app"


def configure_logging(level: str = "INFO") -> None:
    """Configure the application logger. Idempotent — safe to call repeatedly."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)

    # Avoid duplicate handlers if called more than once (e.g. tests, reload).
    if logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `backend/`): `uv run pytest tests/test_logging.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit (await user approval)**

```bash
git add backend/app/core/logging.py backend/tests/test_logging.py
git commit -m "feat: add structured logging setup"
```

---

## Task 5: Postgres (pgvector) via Docker Compose

**Files:**
- Create: `docker-compose.yml` (repo root)

- [ ] **Step 1: Create `docker-compose.yml`** (backend service is added in Task 11)

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: notes_rag_postgres
    environment:
      POSTGRES_USER: notes
      POSTGRES_PASSWORD: notes
      POSTGRES_DB: notes_rag
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U notes -d notes_rag"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
```

- [ ] **Step 2: Validate the compose file**

Run (from repo root): `docker compose config`
Expected: prints the resolved config with no errors.

- [ ] **Step 3: Start Postgres and confirm health**

Run: `docker compose up -d postgres`
Then: `docker compose ps`
Expected: `notes_rag_postgres` is `running (healthy)` within ~15s.

- [ ] **Step 4: Confirm the vector extension is available in the image**

Run: `docker compose exec postgres psql -U notes -d notes_rag -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extname FROM pg_extension WHERE extname='vector';"`
Expected: a row with `vector`.

- [ ] **Step 5: Commit (await user approval)**

```bash
git add docker-compose.yml
git commit -m "feat: add postgres (pgvector) docker-compose service"
```

---

## Task 6: Database base + async session

**Files:**
- Create: `backend/app/db/base.py`, `backend/app/db/session.py`

- [ ] **Step 1: Implement `backend/app/db/base.py`**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
```

- [ ] **Step 2: Implement `backend/app/db/session.py`**

```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def create_engine() -> AsyncEngine:
    """Create the async SQLAlchemy engine from settings."""
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=False, future=True)


engine: AsyncEngine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async DB session."""
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 3: Type-check**

Run (from `backend/`): `uv run mypy app/db`
Expected: `Success: no issues found`.

- [ ] **Step 4: Commit (await user approval)**

```bash
git add backend/app/db/base.py backend/app/db/session.py
git commit -m "feat: add async SQLAlchemy engine and session dependency"
```

---

## Task 7: Alembic migrations (enable vector extension)

**Files:**
- Create: `backend/alembic.ini`, `backend/app/db/migrations/env.py`, `backend/app/db/migrations/script.py.mako`, `backend/app/db/migrations/versions/0001_enable_vector.py`

- [ ] **Step 1: Initialize Alembic with the async template**

Run (from `backend/`): `uv run alembic init -t async app/db/migrations`
Expected: creates `alembic.ini` and `app/db/migrations/` (env.py, script.py.mako, versions/).

- [ ] **Step 2: Point `alembic.ini` at a placeholder URL** — set the `sqlalchemy.url` line to (the real URL is injected in env.py from Settings):

```ini
sqlalchemy.url = driver://user:pass@localhost/dbname
```

- [ ] **Step 3: Replace `backend/app/db/migrations/env.py`** with this (reads URL from Settings, async):

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import get_settings
from app.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 4: Create the initial migration manually** — `backend/app/db/migrations/versions/0001_enable_vector.py`

```python
"""enable pgvector extension

Revision ID: 0001_enable_vector
Revises:
Create Date: 2026-06-13
"""
from alembic import op

revision = "0001_enable_vector"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
```

- [ ] **Step 5: Ensure Postgres is up (Task 5) and run the migration**

Run (from `backend/`, with `backend/.env` present): `uv run alembic upgrade head`
Expected: `Running upgrade  -> 0001_enable_vector`.

- [ ] **Step 6: Verify the extension exists**

Run: `docker compose exec postgres psql -U notes -d notes_rag -c "SELECT extname FROM pg_extension WHERE extname='vector';"`
Expected: one row `vector`.

- [ ] **Step 7: Commit (await user approval)**

```bash
git add backend/alembic.ini backend/app/db/migrations
git commit -m "feat: add alembic with initial pgvector-enable migration"
```

---

## Task 8: Test harness fixtures (conftest)

**Files:**
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: Implement `backend/tests/conftest.py`**

```python
import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Use a dedicated test database URL if provided, else fall back to the dev DB.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://notes:notes@localhost:5432/notes_rag",
)


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
```

- [ ] **Step 2: Sanity-check collection (no tests use fixtures yet)**

Run (from `backend/`): `uv run pytest --collect-only`
Expected: collects existing tests without import errors.

- [ ] **Step 3: Commit (await user approval)**

```bash
git add backend/tests/conftest.py
git commit -m "test: add async db_session and client fixtures"
```

---

## Task 9: Generic BaseRepository

**Files:**
- Create: `backend/app/db/repositories/base.py`
- Test: `backend/tests/test_repository.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_repository.py`

```python
import pytest
import pytest_asyncio
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.repositories.base import BaseRepository


class _Widget(Base):
    __tablename__ = "_test_widgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


@pytest_asyncio.fixture
async def widget_table(db_session):
    engine = db_session.bind
    async with engine.begin() as conn:
        await conn.run_sync(_Widget.__table__.create, checkfirst=True)
    yield
    # Release any locks the test's session still holds (e.g. an open
    # SELECT transaction) before dropping the table, or DROP deadlocks.
    await db_session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(_Widget.__table__.drop, checkfirst=True)


@pytest.mark.asyncio
async def test_create_and_get(db_session, widget_table):
    repo = BaseRepository(_Widget, db_session)

    created = await repo.create(name="alpha")
    await db_session.commit()

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.name == "alpha"


@pytest.mark.asyncio
async def test_get_missing_returns_none(db_session, widget_table):
    repo = BaseRepository(_Widget, db_session)
    assert await repo.get(999999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`, Postgres up): `uv run pytest tests/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db.repositories.base'`.

- [ ] **Step 3: Implement `backend/app/db/repositories/base.py`**

```python
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Generic async CRUD repository for a single ORM model.

    Persistence only — no business logic (that belongs in services).
    """

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def create(self, **values: object) -> ModelT:
        instance = self._model(**values)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def get(self, id_: int) -> ModelT | None:
        return await self._session.get(self._model, id_)

    async def list(self) -> list[ModelT]:
        result = await self._session.execute(select(self._model))
        return list(result.scalars().all())

    async def delete(self, instance: ModelT) -> None:
        await self._session.delete(instance)
        await self._session.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `backend/`): `uv run pytest tests/test_repository.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit (await user approval)**

```bash
git add backend/app/db/repositories/base.py backend/tests/test_repository.py
git commit -m "feat: add generic async BaseRepository"
```

---

## Task 10: Health feature (schema + service + router + app factory)

**Files:**
- Create: `backend/app/schemas/health.py`, `backend/app/services/health.py`, `backend/app/api/health.py`, `backend/app/main.py`
- Test: `backend/tests/test_health.py`

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_health.py`

```python
import pytest

from app.services.health import HealthService


class _FakeSession:
    """Minimal stand-in for AsyncSession.execute used by HealthService."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def execute(self, _stmt):  # noqa: ANN001
        if self._fail:
            raise RuntimeError("db down")
        return object()


@pytest.mark.asyncio
async def test_health_service_ok():
    service = HealthService(_FakeSession())
    status = await service.check()
    assert status.status == "ok"
    assert status.database == "ok"


@pytest.mark.asyncio
async def test_health_service_db_error():
    service = HealthService(_FakeSession(fail=True))
    status = await service.check()
    assert status.status == "ok"
    assert status.database == "error"


@pytest.mark.asyncio
async def test_health_endpoint_returns_200(client):
    resp = await client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] in ("ok", "error")
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`): `uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.health'`.

- [ ] **Step 3: Implement `backend/app/schemas/health.py`**

```python
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok", "error"]
```

- [ ] **Step 4: Implement `backend/app/services/health.py`**

```python
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.health import HealthResponse

logger = logging.getLogger("app")


class HealthService:
    """Business logic for the health check."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(self) -> HealthResponse:
        database = "ok"
        try:
            await self._session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — health must never raise
            logger.exception("Health check DB query failed")
            database = "error"
        return HealthResponse(status="ok", database=database)
```

- [ ] **Step 5: Implement `backend/app/api/health.py`**

```python
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.health import HealthService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(response: Response, session: AsyncSession = Depends(get_db)) -> HealthResponse:
    result = await HealthService(session).check()
    if result.database == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
```

- [ ] **Step 6: Implement `backend/app/main.py`**

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api import health
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Application factory (function by deliberate choice; see spec §3a)."""
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

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
```

- [ ] **Step 7: Run all tests (Postgres up, `backend/.env` present)**

Run (from `backend/`): `uv run pytest -v`
Expected: all tests PASS (config, logging, repository, health unit + endpoint).

- [ ] **Step 8: Manually verify the running app**

Run (from repo root): `make dev`  (equivalently, from `backend/`: `uv run uvicorn app.main:app --reload --port 8000`)
In another shell: `curl http://localhost:8000/health`
Expected: `{"status":"ok","database":"ok"}`. Visit `http://localhost:8000/` → redirects to `/docs`.
Stop the server (Ctrl+C).

- [ ] **Step 9: Lint + type-check clean**

Run (from repo root): `make lint && make typecheck`  (equivalently, from `backend/`: `uv run ruff check . && uv run mypy app`)
Expected: ruff reports no errors; mypy `Success: no issues found`.

- [ ] **Step 10: Commit (await user approval)**

```bash
git add backend/app/schemas/health.py backend/app/services/health.py backend/app/api/health.py backend/app/main.py backend/tests/test_health.py
git commit -m "feat: add health endpoint (handler -> service -> schema)"
```

---

## Task 11: Backend Dockerfile + wire into Compose

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Create `backend/Dockerfile`** (uv-based, multi-stage-lite)

```dockerfile
FROM python:3.13-slim-bookworm

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY app ./app
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Run migrations then start the server
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 2: Create `backend/.dockerignore`**

```dockerignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.env
tests/
```

- [ ] **Step 3: Add the `backend` service to `docker-compose.yml`** — insert under `services:` (after `postgres`):

```yaml
  backend:
    build: ./backend
    container_name: notes_rag_backend
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://notes:notes@postgres:5432/notes_rag
      JWT_SECRET: dev-only-change-me
      ENVIRONMENT: development
      CORS_ORIGINS: '["http://localhost:5173"]'
    ports:
      - "8000:8000"
```

- [ ] **Step 4: Build and run the full stack**

Run (from repo root): `docker compose up --build -d`
Then: `docker compose ps`
Expected: both `notes_rag_postgres` (healthy) and `notes_rag_backend` (running).

- [ ] **Step 5: Verify health through the container**

Run: `curl http://localhost:8000/health`
Expected: `{"status":"ok","database":"ok"}`.

- [ ] **Step 6: Tear down**

Run: `docker compose down`

- [ ] **Step 7: Commit (await user approval)**

```bash
git add backend/Dockerfile backend/.dockerignore docker-compose.yml
git commit -m "feat: containerize backend and wire into compose"
```

---

## Task 12: GitHub Actions CI (lint + type + test)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: notes
          POSTGRES_PASSWORD: notes
          POSTGRES_DB: notes_rag
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U notes -d notes_rag"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql+asyncpg://notes:notes@localhost:5432/notes_rag
      TEST_DATABASE_URL: postgresql+asyncpg://notes:notes@localhost:5432/notes_rag
      JWT_SECRET: ci-secret
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v3
      - name: Set up Python
        run: uv python install 3.13
      - name: Install dependencies
        run: uv sync --frozen
      - name: Lint
        run: uv run ruff check .
      - name: Type-check
        run: uv run mypy app
      - name: Run migrations
        run: uv run alembic upgrade head
      - name: Test
        run: uv run pytest
```

- [ ] **Step 2: Validate YAML locally (optional)**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('valid')"`
Expected: `valid`.

- [ ] **Step 3: Commit (await user approval)**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add lint/type/test workflow with postgres service"
```

> After pushing (when the user chooses to push), confirm the Actions run is green on GitHub.

---

## Task 13: Retarget the devcontainer

**Files:**
- Modify: `.devcontainer/devcontainer.json`

- [ ] **Step 1: Replace `.devcontainer/devcontainer.json`** with:

```json
{
  "name": "notes-rag backend",
  "image": "mcr.microsoft.com/devcontainers/python:1-3.13-bookworm",
  "features": {
    "ghcr.io/va-h/devcontainers-features/uv:1": {}
  },
  "customizations": {
    "vscode": {
      "settings": {},
      "extensions": [
        "ms-python.python",
        "ms-python.vscode-pylance",
        "charliermarsh.ruff"
      ]
    }
  },
  "postCreateCommand": "cd backend && uv sync",
  "forwardPorts": [8000, 5432],
  "portsAttributes": {
    "8000": { "label": "Backend API" }
  }
}
```

- [ ] **Step 2: Commit (await user approval)**

```bash
git add .devcontainer/devcontainer.json
git commit -m "chore: retarget devcontainer to FastAPI backend"
```

---

## Task 14: Learning doc + README

**Files:**
- Create: `docs/learning/00-foundation.md`
- Modify: `README.md`

- [ ] **Step 1: Create `docs/learning/00-foundation.md`** with sections explaining each concept introduced (write real prose, not placeholders):
  - **App factory** — why `create_app()` is a function returning a configured `FastAPI`, and how the test client builds its own instance.
  - **pydantic-settings** — one typed `Settings` object vs scattered `os.getenv`; `.env` loading; `lru_cache`.
  - **Async SQLAlchemy** — engine vs session vs sessionmaker; why `get_db()` yields per-request; `expire_on_commit=False`.
  - **Alembic** — what a migration is; offline vs online; why we inject the URL from Settings; the `CREATE EXTENSION vector` migration.
  - **pgvector** — Postgres extension adding a `vector` type; why we put vectors + app data + checkpoints in one DB.
  - **OOP layering** — Boundary (`api`)/Control (`services`)/Entity (`db`); repository pattern; ABC ports/adapters (preview of Phases 2-3); dependency injection via `Depends`.
  - **pytest fixtures** — `db_session` and `client`; unit (fake session) vs integration (real DB) tests.
  - **uv & make** — env/lock management with uv; the `Makefile` as the task runner (`make dev|test|lint|…`).

- [ ] **Step 2: Replace `README.md`** top-level sections to describe the new stack:
  - Project description (agentic RAG, full-stack), the 5-phase roadmap (link the spec), and Phase 0 status.
  - **Quickstart:** prerequisites (Docker, uv, make); `cp backend/.env.example backend/.env`; `make db`; `cd backend && uv sync`; `make migrate`; `make dev`; visit `http://localhost:8000/docs`.
  - **Full stack:** `make up` (`docker compose up --build -d`).
  - **Dev commands:** `make check` (lint+type+test), and `make test|lint|format|typecheck|migrate|revision m="..."`.
  - Note: the old Streamlit app lives in `legacy/`.

- [ ] **Step 3: Verify quickstart commands match reality** by following them once from a clean shell (Postgres down → up → migrate → dev → curl health). Fix any mismatch in the README.

- [ ] **Step 4: Commit (await user approval)**

```bash
git add docs/learning/00-foundation.md README.md
git commit -m "docs: add foundation learning guide and update README"
```

---

## Definition of Done (verify all)

- [ ] `docker compose up --build` boots Postgres (healthy) + backend.
- [ ] `curl http://localhost:8000/health` → `{"status":"ok","database":"ok"}`.
- [ ] `cd backend && uv run pytest` → all pass.
- [ ] `make lint` and `make typecheck` → clean.
- [ ] CI workflow present; green after push (user-initiated).
- [ ] `.env` gitignored; only `.env.example` committed.
- [ ] `docs/learning/00-foundation.md` complete; README updated.
- [ ] Legacy Streamlit app preserved under `legacy/`.

---

## Self-Review Notes

- **Spec coverage:** §5.1 (health: Task 10), §5.2 (config: Task 3), §5.3 (db + BaseRepository: Tasks 6, 9), §5.4 (compose: Tasks 5, 11), §5.5 (test harness: Task 8), §5.6 (tooling incl. Makefile: Task 2), §5.7 (hygiene: Tasks 1, 3 + prerequisite key rotation), §5.8 (devcontainer + docs: Tasks 13, 14). Alembic vector extension: Task 7. Minimal CI: Task 12.
- **Task runner:** Makefile (per user; make to be installed as a prerequisite).
- **Type consistency:** `HealthResponse(status, database)` used identically in schema, service, router, and tests. `BaseRepository(model, session)` signature matches its test usage. `get_db`/`engine`/`create_app` names consistent across `session.py`, `main.py`, `conftest.py`.
