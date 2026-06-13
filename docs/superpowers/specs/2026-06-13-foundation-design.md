# Phase 0 — Foundation: Design Spec

**Date:** 2026-06-13
**Status:** Approved (pending written-spec review)
**Phase:** 0 of 5 (see "Project Roadmap" below)

---

## 1. Context

`notes-rag` is currently a single-file Streamlit app using LangChain for a lecture-notes
RAG workflow (summary + Q&A) over ChromaDB with HuggingFace MiniLM embeddings and Google
Gemini as the LLM. The goal is to rebuild it as a professional, industrial-standard,
full-stack application:

- **Frontend:** React + Vite + TypeScript
- **Backend:** FastAPI (Python), layered architecture
- **Agent:** LangGraph agentic RAG (conversational memory, query routing/rewriting,
  document grading + self-correction (CRAG), grounded answers with citations)
- **Data:** Postgres + `pgvector` (app data + embeddings + LangGraph checkpoints in one DB)
- **Auth:** Full user auth (JWT login, per-user notes)
- **Ops:** Docker Compose + GitHub Actions CI/CD

The project is **learning/portfolio-oriented**: each phase ships a running-tested vertical
slice and a `docs/learning/` explainer of the new concepts introduced.

Because the project spans multiple subsystems, it is decomposed into sequenced phases.
**This spec covers Phase 0 (Foundation) only.** Each later phase gets its own spec → plan →
build → manual-test cycle.

### Project Roadmap (for context; not all in this spec)

| Phase | Sub-project | Focus |
|------|-------------|-------|
| **0** | **Foundation** (this spec) | Monorepo, FastAPI skeleton, typed config, Postgres+pgvector, migrations, test harness, tooling, minimal CI |
| 1 | Auth | User model, register/login, password hashing, JWT, per-user scoping |
| 2 | Ingestion + retrieval | Upload, parse/chunk, embeddings into pgvector, plain retrieval |
| 3 | Agentic RAG (LangGraph) | State/nodes graph, CRAG, Postgres checkpointer memory, streaming |
| 4 | Frontend | React/Vite/TS: login, upload, streaming chat + citations |
| 5 | CI/CD + polish | Dockerfiles, full compose, GitHub Actions build pipeline, docs |

Build order rationale: **infra → auth → data → intelligence → UI → ops**. Each phase is
independently runnable and testable; nothing depends on a later phase.

---

## 2. Phase 0 Goal

Deliver a running, tested, empty-but-professional backend skeleton talking to
Postgres+pgvector. **No RAG features yet.**

**Definition of done:**

- `docker compose up` boots Postgres (pgvector) + backend
- `GET /health` returns `{"status": "ok", "database": "ok"}` (DB connectivity verified)
- `pytest` passes (at least the health-endpoint test)
- `ruff` (lint + format) and `mypy` (type-check) are clean
- Minimal GitHub Actions CI runs ruff + mypy + pytest on push
- API-key hygiene: only placeholders in committed files (`.env.example`); real `.env` stays
  gitignored. `GOOGLE_API_KEY` is NOT in git history — optional precautionary rotation since
  it surfaced in an AI chat transcript
- `docs/learning/00-foundation.md` explains every new concept

---

## 3. Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Packaging | **uv + `pyproject.toml`** | Fast, modern, industrial direction; replaces pip/venv/requirements.txt |
| Legacy app | **Move to `legacy/`** | Keep for reference until new stack reaches parity, then delete |
| Early CI | **Minimal CI in Phase 0** | ruff + mypy + pytest on push; full build CI still in Phase 5 |
| DB | Postgres + `pgvector` (Docker) | One store for app data + vectors + checkpoints |
| ORM/migrations | SQLAlchemy 2.0 (async) + Alembic | Industry standard; async fits FastAPI |
| Config | `pydantic-settings` | One typed `Settings` object; no scattered `os.getenv` |
| Tests | `pytest` + `pytest-asyncio` + `httpx` | Async test client matches async app |
| Paradigm | **OOP** (service + repository classes, ABC interfaces, DI) | User directive; clean layering and swappable providers |

---

## 3a. Design Principle — Object-Oriented Architecture

The backend uses an object-oriented design throughout:

- **Service classes** hold business logic. Each is a class constructed with its dependencies
  (e.g. a repository or DB session) and exposed to routers via FastAPI dependency injection.
  Phase 0: `HealthService`. Later: `AuthService`, `IngestionService`, `ChatService`.
- **Repository pattern**: all DB access goes through repository classes. A generic
  `BaseRepository` defines common CRUD; concrete repositories subclass it per entity
  (`UserRepository`, `DocumentRepository`, … in later phases). `db/` stays CRUD-only.
- **Abstract provider interfaces (ABCs)**: the swappable infrastructure pieces are defined as
  abstract base classes — `LLMProvider`, `EmbeddingsProvider`, `VectorStore` — with concrete
  implementations (e.g. `GeminiLLMProvider`, `PgVectorStore`) selected via config. This is the
  seam that makes "open to changing the stack" real: business logic depends on the interface,
  not the vendor. (Interfaces are introduced in Phases 2–3; the principle is established now.)
- **Dependency injection**: FastAPI `Depends` constructs and injects service/repository
  instances. No global singletons for stateful collaborators; `Settings` is the one cached
  app-wide object.
- **Idiomatic exception**: the FastAPI app factory `create_app()` stays a *function* (community
  standard). This is a deliberate, documented choice — the OOP lives in services, repositories,
  and providers, not in wrapping the framework's bootstrap.

---

## 3b. Architectural Pattern — Layered + Hexagonal

The backend follows a **layered architecture** with **hexagonal (ports & adapters)** seams for
swappable infrastructure. In a JSON API there is no server-rendered "View", so classic MVC
does not apply directly (the View role belongs to the React frontend + JSON schemas). The
layering is equivalent to EBC's Boundary / Control / Entity split:

| Layer (EBC term) | Folder | Responsibility |
|------------------|--------|----------------|
| Boundary | `api/` (routers) + `schemas/` | Receive requests, validate I/O, serialize responses. No business logic. |
| Control | `services/` | Use-case / business logic, orchestration, rule enforcement. |
| Entity | `db/` (models) + `db/repositories/` | Domain data and CRUD persistence. |

**Hexagonal ports & adapters** sit underneath the Control layer for anything we may swap:
- **Ports** = ABC interfaces (`LLMProvider`, `EmbeddingsProvider`, `VectorStore`).
- **Adapters** = concrete implementations (`GeminiLLMProvider`, `PgVectorStore`, …), chosen via
  `Settings`. Business logic depends only on the port, never the vendor.

Dependency direction: Boundary → Control → (ports) ← Adapters. The Control layer never imports
a concrete adapter directly; adapters are injected. (Ports/adapters land in Phases 2–3; the
layered split is established in Phase 0.)

---

## 4. Target Repository Layout (after Phase 0)

```
notes-rag/
├── backend/
│   ├── app/                   # the application package (import: app.*)
│   │   ├── __init__.py
│   │   ├── main.py            # app factory, CORS, router registration
│   │   ├── api/               # routers (handlers): parse req, call service, format resp
│   │   │   ├── __init__.py
│   │   │   └── health.py      # GET /health
│   │   ├── core/              # cross-cutting infrastructure
│   │   │   ├── __init__.py
│   │   │   ├── config.py      # Settings (pydantic-settings)
│   │   │   └── logging.py     # structured logging setup
│   │   ├── db/                # persistence
│   │   │   ├── __init__.py
│   │   │   ├── base.py        # DeclarativeBase
│   │   │   ├── session.py     # async engine + get_db dependency
│   │   │   ├── repositories/  # repository classes (CRUD only)
│   │   │   │   ├── __init__.py
│   │   │   │   └── base.py    # BaseRepository (generic CRUD)
│   │   │   └── migrations/    # Alembic env + versions
│   │   ├── services/          # business-logic classes
│   │   │   ├── __init__.py
│   │   │   └── health.py      # HealthService
│   │   └── schemas/           # Pydantic request/response models
│   │       ├── __init__.py
│   │       └── health.py      # HealthResponse
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py        # app + DB fixtures
│   │   └── test_health.py
│   ├── alembic.ini
│   ├── pyproject.toml         # deps + ruff + mypy + pytest config
│   ├── Dockerfile
│   └── .env.example
├── legacy/                    # old Streamlit app (app.py, core/, utils/, requirements.txt)
├── docs/
│   ├── superpowers/specs/     # one design doc per phase
│   └── learning/
│       └── 00-foundation.md
├── .github/workflows/ci.yml   # ruff + mypy + pytest
├── .devcontainer/devcontainer.json  # retargeted away from Streamlit
├── docker-compose.yml         # postgres(pgvector) + backend
├── Makefile                   # up / down / test / lint / migrate
├── .gitignore
└── README.md
```

Layer responsibilities (per project conventions):
- `api/` → handlers: parse requests, call injected services, format responses. No business logic.
- `services/` → business-logic classes (Phase 0: `HealthService`).
- `db/` → persistence wiring + repository classes (CRUD only).
- `core/` + `schemas/` → cross-cutting infra and data contracts.

---

## 5. Component Detail

### 5.1 FastAPI skeleton (`main.py`, `api/health.py`, `services/health.py`)

- **App factory** `create_app() -> FastAPI` (function, by deliberate choice — see §3a): builds
  the app, configures CORS (origins from settings), registers routers, wires startup/shutdown
  (logging init, engine disposal).
- `GET /` → redirect to `/docs`.
- `GET /health` → the router (handler) depends on an injected `HealthService` instance and
  returns a `HealthResponse` schema. The handler contains no logic beyond calling the service.
- **`HealthService`** (`services/health.py`): a class constructed with an `AsyncSession`,
  exposing `async def check() -> HealthStatus`. It executes `SELECT 1`; returns
  `{"status": "ok", "database": "ok"}` on success, or `"database": "error"` (router maps to
  HTTP 503) on failure. Establishes the handler→service pattern used everywhere later.

### 5.2 Typed config (`core/config.py`)

- `Settings(BaseSettings)` reading from env / `.env`:
  - `DATABASE_URL` (async DSN, e.g. `postgresql+asyncpg://...`)
  - `GOOGLE_API_KEY` (placeholder in Phase 0; used in Phase 3)
  - `JWT_SECRET` (placeholder; used in Phase 1)
  - `ENVIRONMENT` (`development` | `production`)
  - `CORS_ORIGINS` (list)
- Single cached `get_settings()` accessor.

### 5.3 Database layer (`db/`)

- `base.py`: SQLAlchemy 2.0 `DeclarativeBase`.
- `session.py`: async engine from `DATABASE_URL`, `async_sessionmaker`, and a
  `get_db()` FastAPI dependency yielding an `AsyncSession`.
- `repositories/base.py`: a generic `BaseRepository` class encapsulating common CRUD
  (constructed with a session + model class). Concrete repositories subclass it in later
  phases. No concrete repositories in Phase 0 (no entities yet), but the base + pattern is
  established and unit-tested against a throwaway model if practical.
- **No application tables in Phase 0.** Tables arrive with their owning feature phase.
- Alembic configured for async; initial migration runs `CREATE EXTENSION IF NOT EXISTS vector;`.

### 5.4 Docker Compose (`docker-compose.yml`)

- `postgres` service: `pgvector/pgvector:pg16` image, healthcheck (`pg_isready`), named volume
  for persistence, env from `.env`.
- `backend` service: built from `backend/Dockerfile` (uv-based), depends on healthy postgres,
  runs Alembic migrations then `uvicorn`, exposes `8000`.
- `.env.example` committed with placeholder values; real `.env` gitignored.

### 5.5 Test harness (`tests/`)

- `pytest` + `pytest-asyncio` + `httpx.AsyncClient`.
- `conftest.py`: fixtures for a test app instance and a test DB session (transactional
  rollback per test; uses a dedicated test database / schema).
- `test_health.py`: asserts `GET /health` → 200 and `database == "ok"`.
- Establishes the TDD pattern used in every later phase.

### 5.6 Tooling

- `pyproject.toml`: project metadata, dependencies (managed by uv), and config blocks for
  `ruff` (lint + format), `mypy` (strict-ish), and `pytest`.
- `Makefile` targets: `make up`, `make down`, `make test`, `make lint`, `make migrate`,
  `make revision`.

### 5.7 Security hygiene

- `GOOGLE_API_KEY` is NOT leaked to git: `.env` is untracked and never appeared in any commit.
  Optional precautionary rotation only because the key surfaced in an AI chat transcript
  (done by the user in Google Cloud console; guidance provided). Any new key lives only in the
  local gitignored `.env`.
- `.env.example` contains placeholders only.
- Confirm `.gitignore` covers `.env` (it does) and add `legacy/chroma_db/` if moved.

### 5.8 Devcontainer + docs

- Update `.devcontainer/devcontainer.json`: drop the Streamlit `postAttachCommand` and
  `8501` port; target the new backend (Python 3.11+, uv, forward `8000`). Optionally switch
  to a docker-compose-based devcontainer (deferred unless desired).
- `docs/learning/00-foundation.md`: plain-language explainer of app factory, pydantic-settings,
  async SQLAlchemy, Alembic migrations, pgvector, Docker Compose service wiring, pytest
  fixtures, and the OOP patterns used here (service classes, repository pattern, ABC provider
  interfaces, dependency injection — and *why* each is used).

---

## 6. Out of Scope (Phase 0)

Auth / user model, document & chat tables, ingestion, retrieval, LangGraph, frontend, full
build/deploy CI (Phase 5). The skeleton is intentionally feature-free.

---

## 7. Risks / Notes

- **Async SQLAlchemy + Alembic** has a slightly fiddly setup; the learning doc will cover it.
- **pgvector image vs. extension:** using the `pgvector/pgvector` image means the extension is
  available; the migration still must `CREATE EXTENSION`.
- **API key:** not leaked to git (verified: `.env` untracked, absent from all history).
  Rotation is optional/precautionary and depends on the user's Google Cloud access.
- **Windows host:** Docker Desktop assumed available; Make may need `make` installed (or we
  provide a `justfile` / documented raw commands as fallback).
