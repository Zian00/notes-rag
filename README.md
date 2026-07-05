# Notes RAG

An agentic Retrieval-Augmented Generation application for lecture notes. The project is being rebuilt from a single-file Streamlit prototype into a production-grade full-stack system: a FastAPI + LangGraph backend with a React frontend, persistent storage in Postgres with pgvector, and structured agentic reasoning for retrieval and Q&A.

---

## Roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 0 — Foundation | FastAPI app factory, async SQLAlchemy, Alembic, pgvector, CI | Done |
| 1 — Auth | JWT authentication, user model, protected routes | Done |
| 2 — Ingestion + Retrieval | Document upload, chunking, embedding, vector search | Done |
| 3 — Agentic RAG / LangGraph | LangGraph agent, retrieval tools, LangGraph checkpoints in Postgres | Done |
| 4 — Frontend | React UI, chat interface, upload flow | Done |
| 5 — CI/CD + Polish | Docker image publishing, production config, observability | Upcoming |

Full foundation design spec: [`docs/superpowers/specs/2026-06-13-foundation-design.md`](docs/superpowers/specs/2026-06-13-foundation-design.md)

Frontend design spec: [`docs/superpowers/specs/2026-06-20-frontend-design.md`](docs/superpowers/specs/2026-06-20-frontend-design.md)

---

## Tech Stack

- **FastAPI** — async Python web framework
- **SQLAlchemy (async)** — ORM with asyncpg driver
- **Postgres + pgvector** — relational store and vector similarity search in one database
- **Alembic** — database migration management
- **uv** — Python environment and dependency management
- **Docker + Docker Compose** — local development and full-stack runtime
- **pytest / ruff / mypy** — testing, linting, static type-checking
- **React 19 + Vite + TypeScript** — frontend SPA
- **Tailwind CSS v4 + shadcn/ui** — styling and component primitives
- **TanStack Query** (with `openapi-fetch` / `openapi-react-query`) — typed, cached API access
- **React Router** — client-side routing
- **Vitest / React Testing Library / MSW** — frontend testing

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [make](https://www.gnu.org/software/make/) (available via Git for Windows, Homebrew, or apt)

---

## Quickstart (local dev)

Run the backend against a Dockerised Postgres database, with live reload:

```bash
# 1. Copy the example environment file and fill in any values you need
cp backend/.env.example backend/.env

# 2. Start Postgres (host port 5433 to avoid conflicts with a local Postgres)
make db

# 3. Install Python dependencies into a uv-managed virtual environment
cd backend && uv sync

# 4. Apply database migrations
make migrate

# 5. Start the backend
make dev
```

Then open the interactive API docs at http://localhost:8000/docs.

> **Note:** `make dev` runs through `backend/run_dev.py`. On Linux/macOS it includes live reload.
> On **Windows** it runs **without** reload — the LangGraph Postgres checkpointer uses psycopg3, which
> needs a `SelectorEventLoop` that uvicorn's reloader can't provide on Windows (restart manually after
> edits, or run the full stack with `make up`).

---

## Run the Full Stack in Docker

Build and start both Postgres and the backend together:

```bash
make up
```

The API is available at http://localhost:8000/docs.

To stop and remove the containers:

```bash
make down
```

---

## Frontend (local dev)

The React frontend lives in `frontend/` and talks to the backend over the Vite dev server's
`/api` proxy, so no `.env` file or CORS setup is needed locally (it defaults to `/api`).

```bash
# 1. Make sure the backend is running (see Quickstart above)
make db
make dev

# 2. In a separate terminal, install and run the frontend
cd frontend
npm install
npm run dev
```

Open the URL Vite prints (typically http://localhost:5173).

After backend API changes, regenerate the typed client from the OpenAPI schema — this needs
either the backend running (fetch a fresh `openapi.json` from `http://localhost:8000/openapi.json`)
or just the committed `frontend/openapi.json` snapshot:

```bash
npm run gen:api
```

| Command | What it does |
|---------|--------------|
| `npm run dev` | Start the Vite dev server with hot reload |
| `npm run build` | Type-check and build for production (`frontend/dist/`) |
| `npm run lint` | ESLint check |
| `npm run typecheck` | `tsc --noEmit` project-wide type-check |
| `npm run test` | Run the Vitest suite once |
| `npm run gen:api` | Regenerate `src/api/schema.ts` from `openapi.json` |

---

## Dev Commands

| Command | What it does |
|---------|--------------|
| `make check` | Run lint + type-check + tests in one shot |
| `make test` | Run the pytest suite |
| `make lint` | Ruff lint check |
| `make format` | Ruff auto-format |
| `make typecheck` | mypy static type-check |
| `make migrate` | Apply all pending Alembic migrations |
| `make revision m="message"` | Generate a new Alembic migration from model changes |
| `make dev` | Start the backend with uvicorn live reload |
| `make db` | Start only the Postgres container |
| `make up` | Build and start the full Docker Compose stack |
| `make down` | Stop and remove the Docker Compose stack |

---

## Notes

The original Streamlit prototype is preserved under `legacy/` for reference. It is not part of the active development stack.

---

## Learning

New to any of the patterns or libraries used here? See [`docs/learning/00-foundation.md`](docs/learning/00-foundation.md) for a beginner-friendly explainer covering the app factory, pydantic-settings, async SQLAlchemy, Alembic, pgvector, layered architecture, pytest fixtures, uv, and the Docker Compose port setup.

For the frontend, see [`docs/learning/04-frontend.md`](docs/learning/04-frontend.md) — covers the
Vite dev proxy, the in-memory-access-token + httpOnly-cookie auth model, the typed
`openapi-fetch`/TanStack Query API layer, and the hand-rolled SSE chat streaming parser.
