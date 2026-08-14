# Notes RAG

A full-stack Retrieval-Augmented Generation app for study notes. Upload documents, organize them into groups, and chat with an agentic RAG pipeline that retrieves, grades, and rewrites queries to give grounded answers.

**Live demo:** [notesrag.zheng00.me](https://notesrag.zheng00.me)

## Features

- **Document ingestion** — upload PDF, DOCX, PPTX, TXT, Markdown, and images; OCR fallback for scanned pages
- **Semantic chunking and embedding** — documents are split into token-bounded chunks and embedded with OpenAI text-embedding-3-small
- **Agentic RAG chat** — LangGraph agent decides whether to retrieve, grades retrieved chunks for relevance, and rewrites the query when results are weak
- **Streaming responses** — server-sent events deliver tokens as the model generates them
- **Multi-file attach in chat** — attach up to 5 documents from the chat composer; attachment cards render in message history
- **Groups** — organize documents and conversations into scoped groups; retrieval is strictly walled per group
- **Auth** — JWT access tokens (in-memory) with httpOnly refresh cookies; Argon2id password hashing
- **Dark / light theme**

## Tech Stack

**Frontend** — React 19, Vite, TypeScript, Tailwind CSS v4, shadcn/ui, TanStack Query, React Router, openapi-fetch

**Backend** — FastAPI, SQLAlchemy (async + asyncpg), LangGraph, Pydantic, Alembic

**Data** — Postgres + pgvector (ParadeDB image), procrastinate async job queue

**LLM** — OpenAI gpt-4o-mini (configurable: Google Gemini, Anthropic, OpenAI-compatible)

**Infrastructure** — Docker Compose, Caddy (auto-HTTPS), GitHub Actions CD

**Testing** — pytest + ruff + mypy (285 backend tests), Vitest + React Testing Library + MSW (123 frontend tests)

## Architecture

```
Browser
  |
Caddy (TLS, static SPA, /api reverse proxy)
  |
FastAPI ── procrastinate worker (background ingestion)
  |
LangGraph agent (retrieve → grade → rewrite loop)
  |
Postgres + pgvector
```

Caddy serves the built React SPA at `/` and proxies `/api/*` to the FastAPI backend. The LangGraph agent orchestrates retrieval with tool calls, grades chunk relevance, and optionally rewrites the query before a final generation step. Document ingestion (parsing, chunking, embedding) runs in a separate procrastinate worker process.

## Local Development

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/), [uv](https://docs.astral.sh/uv/getting-started/installation/), [Node.js 22+](https://nodejs.org/), [make](https://www.gnu.org/software/make/)

```bash
# Start Postgres
make db

# Backend
cp backend/.env.example backend/.env   # fill in API keys
cd backend && uv sync
make migrate
make dev                                # http://localhost:8000/docs

# Frontend (separate terminal)
cd frontend && npm install
npm run dev                             # http://localhost:5173
```

Or run everything in Docker:

```bash
make up
```

See [`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md) for detailed setup.

## Production Deployment

The app runs on an Azure VM (Japan East) with Docker Compose and Caddy for auto-HTTPS. Pushing to `main` triggers a GitHub Actions workflow that SSHs into the VM, pulls, rebuilds, and restarts.

Setup guides: [`docs/deploy/01-azure-vm-provisioning.md`](docs/deploy/01-azure-vm-provisioning.md) and [`docs/deploy/02-production-deploy.md`](docs/deploy/02-production-deploy.md)

## Testing

```bash
# Backend (285 tests)
make check          # lint + typecheck + tests

# Frontend (123 tests)
cd frontend
npm test
```
