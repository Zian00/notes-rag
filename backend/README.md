# Backend

Async Python API powering the Notes RAG application. Handles auth, document ingestion, vector search, and agentic RAG chat.

## Stack

- **FastAPI** — async web framework
- **SQLAlchemy** (async) + **asyncpg** — ORM and Postgres driver
- **Alembic** — database migrations
- **Postgres + pgvector** — relational storage and vector similarity search (ParadeDB image bundles both)
- **LangGraph** — agentic RAG pipeline with retrieval grading and corrective query rewriting
- **procrastinate** — async job queue for background document ingestion
- **OpenAI** / **Google Gemini** / **Anthropic** — pluggable LLM and embedding providers
- **Tesseract + Poppler** — OCR for scanned PDFs and images
- **Pydantic** — request/response schemas and settings
- **uv** — dependency management

## Local Setup

```bash
# 1. Start Postgres (from repo root)
make db

# 2. Copy env and fill in API keys
cp .env.example .env

# 3. Install dependencies
uv sync

# 4. Run migrations
uv run alembic upgrade head

# 5. Start the server (with live reload on Linux/macOS)
uv run python run_dev.py
```

The API is available at http://localhost:8000/docs.

The Docker Compose dev setup (`docker-compose.yml` + `docker-compose.override.yml`) also starts a worker process for background ingestion.

## Configuration

All config is loaded from environment variables via `pydantic-settings` (`app/core/config.py`). Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Postgres connection string (asyncpg) | required |
| `JWT_SECRET` | Secret for signing JWT tokens | required |
| `LLM_PROVIDER` | `openai`, `google`, `anthropic`, or `openai_compatible` | `google` (production uses `openai`) |
| `LLM_MODEL` | Model name | `gemini-2.5-flash` (production uses `gpt-4o-mini`) |
| `EMBEDDING_PROVIDER` | `openai` or `google` | `openai` |
| `OPENAI_API_KEY` | OpenAI API key | `""` |
| `GOOGLE_API_KEY` | Google API key | `""` |
| `ENVIRONMENT` | `development` or `production` | `development` |
| `COOKIE_SECURE` | Set `true` in production (HTTPS) | `false` |

See `.env.example` for the full list.

## API Routes

| Prefix | Description |
|--------|-------------|
| `POST /auth/register` | Create account |
| `POST /auth/login` | Login, returns JWT + sets refresh cookie |
| `POST /auth/refresh` | Refresh access token |
| `POST /auth/logout` | Clear refresh cookie |
| `GET /auth/me` | Current user |
| `POST /documents` | Upload a document (multipart) |
| `GET /documents` | List user's documents (optional group filter) |
| `GET /documents/{id}/download` | Download original file |
| `PATCH /documents/{id}` | Update metadata (title, group) |
| `POST /documents/{id}/replace` | Replace file content |
| `DELETE /documents/{id}` | Delete document and its chunks |
| `POST /chat` | Send a message (SSE streaming response) |
| `GET /conversations` | List conversations |
| `GET /conversations/{id}` | Conversation detail with messages |
| `PATCH /conversations/{id}` | Update conversation (title, group) |
| `DELETE /conversations/{id}` | Delete conversation |
| `POST /groups` | Create group |
| `GET /groups` | List groups |
| `PATCH /groups/{id}` | Rename group |
| `DELETE /groups/{id}` | Delete group |
| `POST /search` | Direct vector search (outside chat) |
| `GET /health` | Health check |

## Architecture

```
app/
  api/          Handlers — parse requests, call services, format responses
  services/     Business logic and orchestration
  db/
    models/     SQLAlchemy ORM models
    repositories/  CRUD operations (no business logic)
    migrations/ Alembic migration scripts
  rag/
    graph/      LangGraph agent: nodes, edges, tools, prompts, state
    parsing/    Document parsers (PDF, DOCX, PPTX, images, OCR)
    embeddings.py
    storage.py  File storage backend
  core/         Config, security, logging
  schemas/      Pydantic request/response models
  jobs/         procrastinate task definitions (ingestion worker)
```

### RAG Pipeline (LangGraph)

```mermaid
---
config:
  theme: base
  look: neo
---
flowchart TB
 subgraph condense_step["1. Condense"]
        condense{"First message?"}
        skip["Keep original question"]
        resolve@{ label: "LLM resolves follow-up references\ne.g. 'that topic' → 'backpropagation'" }
  end
 subgraph agent_step["2. Agent"]
        agent["LLM with bound tools\nDecides: answer directly or call a tool?"]
  end
 subgraph tools_step["3. Tools"]
        tools["Execute tool calls"]
        retrieve_notes@{ label: "**retrieve_notes**<br/>Vector search + BM25 keyword\\nscoped to user's group" }
        get_document_content["`**get_document_content**<br/>Fetch all chunks of a specific document`"]
        list_documents@{ label: "**list_documents**<br/>List user's documents — titles, IDs" }
  end
 subgraph grade_step["4. Grade"]
        grade["LLM judges: are chunks relevant\nto the question?"]
  end
 subgraph rewrite_step["5. Rewrite"]
        rewrite["LLM rewrites query\nSees: why it failed + what was retrieved\nFixes vocabulary mismatches"]
  end
 subgraph generate_step["6. Generate"]
        generate["LLM produces final answer\nGrounded strictly in retrieved context\nAdds bracket citations\nRefuses if no context"]
  end
    START(["User sends message"]) --> condense
    condense -- Yes --> skip
    condense -- No --> resolve
    skip --> agent
    resolve --> agent
    agent -- No tool calls\nNo context\nNever searched --> conversational["Conversational reply\ne.g. greeting, list documents"]
    agent -- Tool calls --> tools
    agent -- No tool calls\nHas context or searched --> generate
    conversational --> END_conv(["END — SSE stream to client"])
    tools --> retrieve_notes & get_document_content & list_documents
    retrieve_notes -- Chunks returned --> grade
    get_document_content -- Content returned --> agent
    list_documents -- List returned --> agent
    grade -- Relevant --> generate
    grade -- Not relevant\nRetries &lt; 2 --> rewrite
    rewrite --> agent
    grade -- Not relevant\nRetries exhausted --> generate
    generate --> END_gen(["END — SSE stream with citations"])

    resolve@{ shape: rect}
    retrieve_notes@{ shape: rect}
    list_documents@{ shape: rect}
```

The agent can also answer directly (greetings, listing documents) without retrieving. Checkpoints are stored in Postgres so conversations resume across sessions.

## Testing

285 tests covering API endpoints, services, repositories, RAG graph nodes, parsing, and embeddings.

```bash
# Run all
uv run pytest

# With make (from repo root)
make test       # tests
make lint       # ruff
make typecheck  # mypy
make check      # all three
```
