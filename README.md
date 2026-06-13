# Notes RAG

An agentic Retrieval-Augmented Generation application for lecture notes. The project is being rebuilt from a single-file Streamlit prototype into a production-grade full-stack system: a FastAPI + LangGraph backend with a React frontend, persistent storage in Postgres with pgvector, and structured agentic reasoning for retrieval and Q&A.

---

## Roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 0 — Foundation | FastAPI app factory, async SQLAlchemy, Alembic, pgvector, CI | **Current** |
| 1 — Auth | JWT authentication, user model, protected routes | Upcoming |
| 2 — Ingestion + Retrieval | Document upload, chunking, embedding, vector search | Upcoming |
| 3 — Agentic RAG / LangGraph | LangGraph agent, retrieval tools, LangGraph checkpoints in Postgres | Upcoming |
| 4 — Frontend | React UI, chat interface, upload flow | Upcoming |
| 5 — CI/CD + Polish | Docker image publishing, production config, observability | Upcoming |

Full foundation design spec: [`docs/superpowers/specs/2026-06-13-foundation-design.md`](docs/superpowers/specs/2026-06-13-foundation-design.md)

---

## Tech Stack

- **FastAPI** — async Python web framework
- **SQLAlchemy (async)** — ORM with asyncpg driver
- **Postgres + pgvector** — relational store and vector similarity search in one database
- **Alembic** — database migration management
- **uv** — Python environment and dependency management
- **Docker + Docker Compose** — local development and full-stack runtime
- **pytest / ruff / mypy** — testing, linting, static type-checking

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

# 5. Start the backend with live reload
make dev
```

Then open the interactive API docs at http://localhost:8000/docs.

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
