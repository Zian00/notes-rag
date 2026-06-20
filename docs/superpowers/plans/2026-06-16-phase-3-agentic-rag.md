# Phase 3 — Agentic RAG / LangGraph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put an LLM on top of Phase 2 retrieval. An authenticated user asks a natural-language question and gets a **grounded, cited answer drawn only from their own notes**, streamed via SSE, inside a **persisted multi-turn conversation**. The agent has **3 tools** (`retrieve_notes`, `list_documents`, `get_document_content`) supporting Q&A plus topic and whole-document summaries. The generation LLM is **swappable by config** (Gemini default; Claude / local OpenAI-compatible drop-in). A **combined agentic + corrective** LangGraph graph drives it, with an `AGENTIC_RETRIEVAL` fallback flag for weak/local models.

**Architecture:** Layered + hexagonal, matching Phases 0–2. The LangGraph graph lives in `app/rag/graph/`; the configurable LLM is a factory (`app/rag/llm.py`) returning a LangChain `BaseChatModel` (NOT a hand-rolled ABC — preserves tool-calling + streaming). `ChatService` orchestrates conversation lifecycle + streaming and uses its **own `async_sessionmaker`** (not the request session) so DB access survives the streaming response. Graph tools reuse Phase 2's `RetrievalService` / repositories, also via the sessionmaker, scoped to `user_id` passed through LangGraph `config`. Conversation message state is persisted by `AsyncPostgresSaver`; a small `conversations` table adds listing/titling.

**Tech Stack:** FastAPI (SSE via `StreamingResponse`), SQLAlchemy 2.0 async (asyncpg), LangGraph (`StateGraph`, custom `TypedDict` state, conditional edges), `langgraph-checkpoint-postgres` (`AsyncPostgresSaver` on a **psycopg3** `AsyncConnectionPool`), `langchain`/`langchain-core` (`init_chat_model`, `bind_tools`, `with_structured_output`), `langchain-google-genai` (`gemini-2.5-flash`). Tests: pytest + pytest-asyncio + httpx against `notes_rag_test`, with a scripted `FakeChatModel`, fake embeddings, and `InMemorySaver` for graph/service tests; one real-`AsyncPostgresSaver` integration test.

**Spec:** `docs/superpowers/specs/2026-06-16-agentic-rag-design.md`

> **Commit policy (user standing rule):** NEVER auto-commit. Each milestone ends with a commit step, but the executor must STOP at each **milestone boundary** and let the user run the commit themselves. Group commits per milestone unless the user says otherwise.

> **Subagent safety rules (standing):** Do NOT run destructive DB ops (DROP/TRUNCATE/DELETE) outside the test harness, and do NOT modify test infrastructure (conftest, CI, Docker) beyond what a task explicitly authorizes.

---

## File Structure

**New runtime modules**
- `app/rag/llm.py` — `build_chat_model(settings) -> BaseChatModel` (the configurable-LLM factory)
- `app/rag/graph/__init__.py` — re-export `build_rag_graph`, `RagState`
- `app/rag/graph/state.py` — `RagState` TypedDict
- `app/rag/graph/prompts.py` — system prompts (grounding, grade, rewrite)
- `app/rag/graph/tools.py` — `build_tools(...)` → the 3 user-scoped tools + `format_chunks`/`citation` helpers
- `app/rag/graph/nodes.py` — `agent`, `tools_node`, `grade`, `rewrite`, `generate` node builders + routers
- `app/rag/graph/builder.py` — `build_rag_graph(chat_model, embeddings, sessionmaker, settings, checkpointer)`
- `app/models/conversation.py` — `Conversation` ORM model
- `app/db/repositories/conversation.py` — `ConversationRepository`
- `app/services/chat.py` — `ChatService` + domain errors (`ConversationNotFound`)
- `app/schemas/chat.py` — `ChatRequest`, `Citation`, `ConversationResponse`, `ConversationDetail`, `MessageResponse`
- `app/api/chat.py` — `POST /chat` (SSE)
- `app/api/conversations.py` — `GET /conversations`, `GET /conversations/{id}`, `DELETE /conversations/{id}`
- `app/db/migrations/versions/0004_conversations.py` — migration (the `conversations` table only)

**Modified**
- `app/core/config.py` — new Settings fields + `checkpointer_conninfo` property
- `app/models/__init__.py` — register `Conversation`
- `app/db/repositories/chunk.py` — add `get_for_document(...)`
- `app/api/deps.py` — `get_chat_graph`, `get_chat_service`
- `app/main.py` — lifespan: psycopg pool + `AsyncPostgresSaver.setup()` + compile graph onto `app.state`; register routers
- `pyproject.toml` — new deps

**New test modules**
- `tests/fakes.py` — add `FakeChatModel` (scripted; supports `bind_tools`, `with_structured_output`, `ainvoke`, `astream`)
- `tests/test_config_phase3.py`, `tests/test_conversation_repository.py`, `tests/test_chunk_repository_get_for_document.py`,
  `tests/test_llm_factory.py`, `tests/test_graph_tools.py`, `tests/test_graph_nodes.py`, `tests/test_graph_flow.py`,
  `tests/test_chat_service.py`, `tests/test_chat_api.py`, `tests/test_conversations_api.py`,
  `tests/test_checkpointer_integration.py`

---

## Milestone A — Config, deps, data model, migration, repo method

### Task 1: Add dependencies

**Files:** Modify `backend/pyproject.toml`

- [ ] **Step 1: Add runtime deps via uv**
```bash
cd backend
uv add "langgraph>=0.2.60" "langgraph-checkpoint-postgres>=2.0" \
       "langchain>=0.3" "langchain-core>=0.3" "langchain-google-genai>=2.0" \
       "psycopg[binary]>=3.2" "psycopg-pool>=3.2"
```
Expected: `pyproject.toml` `dependencies` gains the 7 packages; `uv.lock` updates.
*(Add-when-switching, NOT now: `langchain-anthropic`, `langchain-openai`. The factory raises a clear error if a selected provider's package is missing.)*

- [ ] **Step 2: Verify install**
```bash
cd backend && uv run python -c "import langgraph, langchain, langchain_core, langchain_google_genai, psycopg, psycopg_pool; from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver; print('ok')"
```
Expected: prints `ok`.

---

### Task 2: Extend Settings

**Files:** Modify `app/core/config.py`; Test `tests/test_config_phase3.py`

- [ ] **Step 1: Write the failing test** — `tests/test_config_phase3.py`:
```python
from app.core.config import Settings


def _settings(**over: object) -> Settings:
    base = {"database_url": "postgresql+asyncpg://u:p@localhost:5432/db", "jwt_secret": "x"}
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def test_phase3_defaults():
    s = _settings()
    assert s.llm_provider == "google"
    assert s.llm_model == "gemini-2.5-flash"
    assert s.llm_temperature == 0.2
    assert s.llm_base_url is None
    assert s.agentic_retrieval is True
    assert s.max_grade_retries == 2
    assert s.chat_history_limit == 20


def test_checkpointer_conninfo_strips_asyncpg():
    s = _settings()
    # psycopg3 needs a driver-less URL; asyncpg's "+asyncpg" must be removed.
    assert s.checkpointer_conninfo == "postgresql://u:p@localhost:5432/db"
```

- [ ] **Step 2: Add the fields + property** in `app/core/config.py` (match existing style; `llm_model` already exists — keep it). Add near the other fields:
```python
from typing import Literal  # if not already imported

    # --- Phase 3: agentic RAG / LLM ---
    llm_provider: Literal["google", "anthropic", "openai_compatible"] = "google"
    # llm_model already declared (default "gemini-2.5-flash")
    llm_temperature: float = 0.2          # low → grounded, less drift
    llm_base_url: str | None = None       # for openai_compatible (local Ollama/vLLM)
    anthropic_api_key: str = ""           # only used when llm_provider == "anthropic"
    agentic_retrieval: bool = True        # False → deterministic always-retrieve path
    max_grade_retries: int = 2            # corrective rewrite cap
    chat_history_limit: int = 20          # max prior messages fed to the model
```
And a property (psycopg3 can't parse the `+asyncpg` SQLAlchemy driver suffix):
```python
    @property
    def checkpointer_conninfo(self) -> str:
        """psycopg3 conninfo for the LangGraph checkpointer (strip the asyncpg driver suffix)."""
        return self.database_url.replace("+asyncpg", "")
```

- [ ] **Step 3: Run** `uv run pytest tests/test_config_phase3.py -q` → green. Then `uv run ruff check . && uv run mypy .`.

---

### Task 3: Conversation model + migration

**Files:** New `app/models/conversation.py`; Modify `app/models/__init__.py`; New `app/db/migrations/versions/0004_conversations.py`

- [ ] **Step 1: Model** — `app/models/conversation.py` (match the column/typing style of `app/models/document.py`):
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base  # use the same Base import path as document.py


class Conversation(Base):
    """One chat thread. `id` doubles as the LangGraph checkpointer thread_id."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Title is derived from the first question (truncated); nullable until the first turn.
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```
> Verify the exact `Base` import and `created_at`/`updated_at` idiom against `app/models/document.py` and match it (don't introduce a new style).

- [ ] **Step 2: Register** in `app/models/__init__.py` — add `from app.models.conversation import Conversation` and include in `__all__`.

- [ ] **Step 3: Migration** — `app/db/migrations/versions/0004_conversations.py`, `down_revision = "0003"` (confirm the 0003 revision id string). Creates the `conversations` table + `ix_conversations_user_id`. Hand-write `upgrade()`/`downgrade()` matching the style of `0003_documents_chunks.py`. **Do NOT create checkpointer tables here** — `AsyncPostgresSaver.setup()` owns those.

- [ ] **Step 4: Apply + verify** `make migrate` (or `uv run alembic upgrade head`); confirm `conversations` exists. Then `uv run ruff check . && uv run mypy .`.

---

### Task 4: ConversationRepository

**Files:** New `app/db/repositories/conversation.py`; Test `tests/test_conversation_repository.py`

- [ ] **Step 1: Failing tests** — create/get-ownership/list-ordering/delete/touch. Key assertions: `get_for_user` returns `None` for another user's id; `list_for_user` newest-first by `updated_at`; `touch` bumps `updated_at`.

- [ ] **Step 2: Implement** (match `DocumentRepository` base-class style):
```python
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository  # match document.py's base import
from app.models.conversation import Conversation


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Conversation, session)

    async def create(self, *, user_id: uuid.UUID, title: str | None) -> Conversation:
        convo = Conversation(user_id=user_id, title=title)
        self._session.add(convo)
        await self._session.flush()  # populate id
        return convo

    async def get_for_user(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def touch(self, conversation_id: uuid.UUID) -> None:
        # Bump updated_at so the conversation rises to the top of the list after a turn.
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())  # import func from sqlalchemy
        )

    async def delete(self, conversation_id: uuid.UUID) -> None:
        convo = await self._session.get(Conversation, conversation_id)
        if convo is not None:
            await self._session.delete(convo)
```
> Confirm `BaseRepository` exists and its constructor signature by reading `app/db/repositories/base.py` (or whatever `document.py` extends). If repos don't use a base class, mirror `document.py` exactly instead.

- [ ] **Step 3:** `uv run pytest tests/test_conversation_repository.py -q` → green; ruff + mypy clean.

---

### Task 5: ChunkRepository.get_for_document

**Files:** Modify `app/db/repositories/chunk.py`; Test `tests/test_chunk_repository_get_for_document.py`

- [ ] **Step 1: Failing test** — insert two docs (different users), assert `get_for_document(doc_id, owner_id)` returns that doc's chunks **ordered by `chunk_index`**, and returns `[]` for a non-owner.

- [ ] **Step 2: Implement** — add to `ChunkRepository`:
```python
    async def get_for_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[DocumentChunk]:
        """All chunks of one document, in order — for whole-document summarisation.
        Scoped to the owner so a user can never fetch another user's document."""
        stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.user_id == user_id,
            )
            .order_by(DocumentChunk.chunk_index)
        )
        return list((await self._session.execute(stmt)).scalars().all())
```

- [ ] **Step 3:** `uv run pytest tests/test_chunk_repository_get_for_document.py -q` → green; ruff + mypy clean.

- [ ] **MILESTONE A — STOP. Suggested commit (user runs it):**
```bash
git add -A && git commit -m "feat(phase3): config, conversations model+repo, chunk get_for_document, deps"
```

---

## Milestone B — Configurable LLM factory

### Task 6: build_chat_model

**Files:** New `app/rag/llm.py`; Test `tests/test_llm_factory.py`

- [ ] **Step 1: Failing tests** — `tests/test_llm_factory.py` (patch the provider classes so no network/keys are needed):
```python
import pytest

from app.core.config import Settings
from app.rag.llm import build_chat_model


def _settings(**over):
    base = {"database_url": "postgresql+asyncpg://u:p@h:5432/db", "jwt_secret": "x"}
    return Settings(**{**base, **over})


def test_google_provider(monkeypatch):
    captured = {}

    class FakeChat:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("app.rag.llm.ChatGoogleGenerativeAI", FakeChat)
    model = build_chat_model(_settings(google_api_key="k", llm_model="gemini-2.5-flash"))
    assert isinstance(model, FakeChat)
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["temperature"] == 0.2


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        build_chat_model(_settings(llm_provider="mystery"))  # type: ignore[arg-type]


def test_openai_compatible_requires_base_url():
    with pytest.raises(ValueError):
        build_chat_model(_settings(llm_provider="openai_compatible", llm_base_url=None))
```

- [ ] **Step 2: Implement** `app/rag/llm.py`:
```python
"""Configurable chat-LLM factory.

Returns a LangChain ``BaseChatModel`` selected by ``settings.llm_provider``. We use a
factory (not a hand-rolled ABC) because LangGraph's tool-calling + token streaming
integrate with ``BaseChatModel`` (``.bind_tools``, ``.astream``); re-implementing that
behind a custom interface would be costly and fragile. Swapping Gemini → Claude →
local (Ollama/vLLM via an OpenAI-compatible endpoint) is config-only; the graph never
names a provider.
"""

from langchain_core.language_models import BaseChatModel

from app.core.config import Settings

# Imported at module top so tests can monkeypatch the symbol. Only the google provider
# is a hard dependency in Phase 3; anthropic/openai packages are add-when-switching and
# imported lazily inside the factory so their absence doesn't break import.
from langchain_google_genai import ChatGoogleGenerativeAI


def build_chat_model(settings: Settings) -> BaseChatModel:
    provider = settings.llm_provider
    if provider == "google":
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            google_api_key=settings.google_api_key or None,
        )
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # add-when-switching dependency
            raise ValueError(
                "llm_provider='anthropic' needs `uv add langchain-anthropic`"
            ) from exc
        return ChatAnthropic(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            api_key=settings.anthropic_api_key or None,
        )
    if provider == "openai_compatible":
        if not settings.llm_base_url:
            raise ValueError("llm_provider='openai_compatible' requires llm_base_url")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ValueError(
                "llm_provider='openai_compatible' needs `uv add langchain-openai`"
            ) from exc
        return ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            base_url=settings.llm_base_url,
            api_key="not-needed",  # local servers ignore the key
        )
    raise ValueError(f"Unknown llm_provider: {provider!r}")
```

- [ ] **Step 3:** `uv run pytest tests/test_llm_factory.py -q` → green; ruff + mypy clean.

- [ ] **MILESTONE B — STOP. Suggested commit:**
```bash
git add -A && git commit -m "feat(phase3): configurable chat-LLM factory (google default; claude/local drop-in)"
```

---

## Milestone C — LangGraph graph (state, tools, nodes, builder)

> This is the heart of the phase. Build bottom-up: state → tools → nodes → builder, each tested with the scripted `FakeChatModel` + `InMemorySaver`.

### Task 7: Graph state + the test FakeChatModel

**Files:** New `app/rag/graph/state.py`, `app/rag/graph/__init__.py`; Modify `tests/fakes.py`

- [ ] **Step 1: `app/rag/graph/state.py`:**
```python
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class RagState(TypedDict, total=False):
    # Conversation messages. add_messages appends (and merges streamed chunks);
    # the checkpointer persists this across turns keyed by thread_id.
    messages: Annotated[list[AnyMessage], add_messages]
    question: str            # current (possibly rewritten) query
    context: list[dict[str, Any]]  # chunks from the latest retrieve/get-document (for grounding + citations)
    relevant: bool           # grade verdict on `context`
    retry_count: int         # rewrites so far (capped at max_grade_retries)
```

- [ ] **Step 2: `app/rag/graph/__init__.py`** — re-export:
```python
from app.rag.graph.builder import build_rag_graph
from app.rag.graph.state import RagState

__all__ = ["build_rag_graph", "RagState"]
```
*(This import will fail until Task 11 creates builder.py — that's fine; create `__init__.py` now with just the `RagState` export and add `build_rag_graph` in Task 11, or create the file in Task 11. Keep ruff happy by ordering tasks so the package imports cleanly at each green checkpoint.)*

- [ ] **Step 3: Add `FakeChatModel` to `tests/fakes.py`** — a scripted double supporting the methods the graph uses. It returns pre-queued responses in order; `bind_tools` returns self; `with_structured_output(Schema)` returns a runnable yielding the next queued object; `astream` yields the next AIMessage as a single chunk.
```python
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class _StructuredRunnable:
    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    async def ainvoke(self, *_a: Any, **_k: Any) -> Any:
        return self._parent._next()  # next queued structured object (e.g. Grade)


class FakeChatModel(BaseChatModel):
    """Deterministic, scripted chat model for graph/service tests.

    Queue responses with `FakeChatModel(responses=[...])`. Each entry is either an
    AIMessage (for agent/generate/rewrite) or a pydantic object (for with_structured_output grading).
    """

    responses: list[Any] = []
    _i: int = 0

    class Config:
        arbitrary_types_allowed = True

    def _next(self) -> Any:
        r = self.responses[self._i]
        object.__setattr__(self, "_i", self._i + 1)
        return r

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, *_a: Any, **_k: Any) -> "FakeChatModel":
        return self

    def with_structured_output(self, *_a: Any, **_k: Any) -> _StructuredRunnable:
        return _StructuredRunnable(self)

    def _generate(self, *_a: Any, **_k: Any) -> ChatResult:
        msg = self._next()
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(self, *_a: Any, **_k: Any) -> ChatResult:
        msg = self._next()
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _astream(self, *_a: Any, **_k: Any) -> AsyncIterator[Any]:
        msg = self._next()
        # Stream the queued answer as a couple of chunks so token-streaming tests see deltas.
        text = msg.content if isinstance(msg, BaseMessage) else str(msg)
        mid = max(1, len(text) // 2)
        yield ChatGeneration(message=AIMessageChunk(content=text[:mid]))
        yield ChatGeneration(message=AIMessageChunk(content=text[mid:]))
```
> The exact `BaseChatModel` streaming contract can vary by langchain version. If `_astream` signature/return type mismatches at runtime, adjust to the installed version (the executor should run a tiny smoke test: stream one node and confirm chunks arrive). Keep the *intent*: scripted, deterministic, supports `bind_tools` + `with_structured_output` + streaming.

- [ ] **Step 4:** ruff + mypy clean. (No dedicated test yet; exercised by Tasks 8/10/11.)

---

### Task 8: Tools

**Files:** New `app/rag/graph/tools.py`; Test `tests/test_graph_tools.py`

- [ ] **Step 1: Failing tests** — with fake embeddings + a real test session (via the `_engine`/sessionmaker fixtures), seed a document + chunks and assert:
  - `retrieve_notes.ainvoke({"query": "..."}, config=cfg)` returns structured chunk dicts scoped to `cfg["configurable"]["user_id"]`, honouring `course`/`tags`.
  - `list_documents` returns only the caller's documents.
  - `get_document_content` returns one document's chunks **in `chunk_index` order**, and **empty for a non-owner**.
  - Each tool reads `user_id` from the injected `config` (not an LLM-supplied arg).

- [ ] **Step 2: Implement `app/rag/graph/tools.py`** — a builder capturing infra; tools read per-request context from `config`:
```python
"""The 3 user-scoped tools the agent can call.

Tools are closures over infra (embeddings + sessionmaker) built once at startup.
Per-request context (user_id, default filters, top_k) flows in through LangGraph's
``config["configurable"]`` and is read via the injected ``RunnableConfig`` — it is NOT
exposed to the LLM, so the model can never widen scope beyond the caller's own rows.
Each tool opens its OWN short-lived session (not the request session) so DB access is
safe inside the long-lived compiled graph and during streaming responses.
"""

import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool, tool
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.rag.embeddings import EmbeddingsProvider
from app.services.retrieval import RetrievalService

SessionMaker = async_sessionmaker


def _user_id(config: RunnableConfig) -> uuid.UUID:
    return config["configurable"]["user_id"]


def _chunk_to_dict(c: Any) -> dict[str, Any]:
    # Shared shape used for grounding text AND citations.
    return {
        "chunk_id": str(c.chunk_id),
        "document_id": str(c.document_id),
        "filename": c.filename,
        "title": c.title,
        "content": c.content,
        "page_number": c.page_number,
        "section": c.section,
        "score": getattr(c, "score", None),
    }


def build_tools(
    embeddings: EmbeddingsProvider,
    sessionmaker: SessionMaker,
    default_top_k: int,
) -> list[StructuredTool]:
    @tool
    async def retrieve_notes(
        query: str,
        course: str | None = None,
        tags: list[str] | None = None,
        *,
        config: RunnableConfig,
    ) -> list[dict[str, Any]]:
        """Search the user's lecture notes for passages relevant to the query.
        Use for questions and topic summaries. Optionally narrow by course or tags."""
        cfg = config["configurable"]
        async with sessionmaker() as session:
            service = RetrievalService(ChunkRepository(session), embeddings, default_top_k)
            results = await service.search(
                _user_id(config),
                query,
                top_k=cfg.get("top_k"),
                course=course or cfg.get("course"),
                tags=tags or cfg.get("tags"),
            )
        return [_chunk_to_dict(r) for r in results]

    @tool
    async def list_documents(
        course: str | None = None,
        *,
        config: RunnableConfig,
    ) -> list[dict[str, Any]]:
        """List the user's uploaded documents (id, title, filename, course).
        Use to answer 'what notes do I have?' or to find a document's id before summarising it."""
        async with sessionmaker() as session:
            docs = await DocumentRepository(session).list_for_user(_user_id(config), course=course)
        return [
            {
                "document_id": str(d.id),
                "title": d.title,
                "filename": d.filename,
                "course": d.course,
            }
            for d in docs
        ]

    @tool
    async def get_document_content(
        document_id: str,
        *,
        config: RunnableConfig,
    ) -> list[dict[str, Any]]:
        """Fetch the full text of one document, in order, for a whole-document summary.
        Pass a document_id from list_documents."""
        async with sessionmaker() as session:
            chunks = await ChunkRepository(session).get_for_document(
                uuid.UUID(document_id), _user_id(config)
            )
        # Reuse the citation shape; score is irrelevant for a deliberate full-doc fetch.
        return [
            {
                "chunk_id": str(c.id),
                "document_id": str(c.document_id),
                "filename": None,
                "title": c.section,
                "content": c.content,
                "page_number": c.page_number,
                "section": c.section,
                "score": None,
            }
            for c in chunks
        ]

    return [retrieve_notes, list_documents, get_document_content]


def format_chunks_for_llm(chunks: list[dict[str, Any]]) -> str:
    """Render retrieved chunks as a numbered, cite-able context block for the model."""
    if not chunks:
        return "NO RESULTS."
    lines = []
    for i, c in enumerate(chunks, 1):
        loc = f" (p.{c['page_number']})" if c.get("page_number") else ""
        lines.append(f"[{i}] {c.get('title') or c.get('filename') or 'note'}{loc}\n{c['content']}")
    return "\n\n".join(lines)
```
> `@tool` async functions with an injected `config: RunnableConfig` param: LangChain auto-injects `config` and hides it from the model's schema. Confirm against the installed langchain-core version during execution (run one tool via `.ainvoke(args, config=...)` in the test).

- [ ] **Step 3:** `uv run pytest tests/test_graph_tools.py -q` → green; ruff + mypy clean.

---

### Task 9: Prompts

**Files:** New `app/rag/graph/prompts.py`

- [ ] **Step 1: Implement** (no test; consumed by nodes):
```python
# System prompt for the agent node: decide whether to use a tool.
AGENT_SYSTEM = (
    "You are a study assistant for a student's personal lecture notes. "
    "To answer questions or write topic summaries, call `retrieve_notes`. "
    "To list what notes exist or resolve a named document, call `list_documents`. "
    "To summarise a whole document, call `list_documents` then `get_document_content`. "
    "If the user's message needs no notes (greetings, meta questions about this chat), answer directly."
)

# System prompt for the generate node: STRICT grounding (anti-hallucination).
GENERATE_SYSTEM = (
    "Answer using ONLY the provided context from the student's notes. "
    "Cite sources inline like [1], [2] matching the numbered context. "
    "If the context does not contain the answer, say clearly: "
    "\"I couldn't find this in your notes.\" Do not use outside knowledge."
)

GRADE_SYSTEM = (
    "You judge whether the retrieved context is relevant to the user's question. "
    "Answer strictly with the structured schema."
)

REWRITE_SYSTEM = (
    "The previous search returned weak results. Rewrite the user's question into a better "
    "search query that is specific and keyword-rich. Return ONLY the rewritten query."
)
```

---

### Task 10: Nodes + routers

**Files:** New `app/rag/graph/nodes.py`; Test `tests/test_graph_nodes.py`

- [ ] **Step 1: Failing tests** (use `FakeChatModel` + the tools from Task 8, no graph yet — call node callables directly):
  - `agent` with a queued tool-call AIMessage → returns a message with `tool_calls`; with a queued plain AIMessage → no tool calls.
  - `tools_node` executes a queued `retrieve_notes` tool call → appends a `ToolMessage` and sets `state["context"]`; a `list_documents` call sets no context.
  - `grade` sets `state["relevant"]` from the structured verdict.
  - `rewrite` increments `retry_count` and appends a rewritten human message.
  - `generate` returns an AIMessage; with empty context the prompt forces the refusal (assert the model was asked to ground — i.e. context block contained "NO RESULTS").
  - Routers: `route_after_agent` → "tools" vs "generate"; `route_after_tools` → "grade" only after `retrieve_notes`; `route_after_grade` → "generate" when relevant, "rewrite" when weak + retries left, "generate" when retries exhausted.

- [ ] **Step 2: Implement `app/rag/graph/nodes.py`** — node builders capture the model + settings; nodes are pure-ish `(state, config) -> dict`:
```python
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END
from pydantic import BaseModel, Field

from app.rag.graph.prompts import AGENT_SYSTEM, GENERATE_SYSTEM, GRADE_SYSTEM, REWRITE_SYSTEM
from app.rag.graph.state import RagState
from app.rag.graph.tools import format_chunks_for_llm

_RETRIEVAL_TOOLS = {"retrieve_notes", "get_document_content"}  # these populate `context`


class Grade(BaseModel):
    relevant: bool = Field(description="True if the context can answer the question.")


def make_nodes(
    model: BaseChatModel,
    tools: list[StructuredTool],
    history_limit: int,
    max_retries: int,
) -> dict[str, Any]:
    tools_by_name = {t.name: t for t in tools}
    agent_model = model.bind_tools(tools)
    grader = model.with_structured_output(Grade)

    def _recent(state: RagState) -> list[Any]:
        return state["messages"][-history_limit:]

    async def agent(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        msgs = [SystemMessage(AGENT_SYSTEM), *_recent(state)]
        resp = await agent_model.ainvoke(msgs, config)
        return {"messages": [resp]}

    async def tools_node(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        last = state["messages"][-1]
        out_msgs: list[Any] = []
        context = state.get("context", [])
        for tc in last.tool_calls:  # type: ignore[attr-defined]
            result = await tools_by_name[tc["name"]].ainvoke(tc["args"], config=config)
            out_msgs.append(
                ToolMessage(content=format_chunks_for_llm(result)
                            if isinstance(result, list) else str(result),
                            tool_call_id=tc["id"])
            )
            if tc["name"] in _RETRIEVAL_TOOLS and isinstance(result, list):
                context = result  # latest retrieval becomes the grounding context
        return {"messages": out_msgs, "context": context}

    async def grade(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        ctx = format_chunks_for_llm(state.get("context", []))
        verdict: Grade = await grader.ainvoke(
            [SystemMessage(GRADE_SYSTEM),
             HumanMessage(f"Question: {state['question']}\n\nContext:\n{ctx}")],
            config,
        )
        return {"relevant": verdict.relevant}

    async def rewrite(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        resp = await model.ainvoke(
            [SystemMessage(REWRITE_SYSTEM), HumanMessage(state["question"])], config
        )
        new_q = resp.content if isinstance(resp.content, str) else str(resp.content)
        return {
            "question": new_q,
            "retry_count": state.get("retry_count", 0) + 1,
            "messages": [HumanMessage(new_q)],
        }

    async def generate(state: RagState, config: RunnableConfig) -> dict[str, Any]:
        ctx = format_chunks_for_llm(state.get("context", []))
        resp = await model.ainvoke(
            [SystemMessage(GENERATE_SYSTEM),
             *_recent(state),
             HumanMessage(f"Context:\n{ctx}")],
            config,
        )
        return {"messages": [resp]}

    return {"agent": agent, "tools": tools_node, "grade": grade,
            "rewrite": rewrite, "generate": generate}


# --- routers (plain functions on state; easy to unit test) ---

def route_after_agent(state: RagState) -> Literal["tools", "generate"]:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "generate"


def route_after_tools(state: RagState) -> Literal["grade", "agent"]:
    last_tool = state["messages"][-1]
    # If the most recent tool was a retrieval, grade it; otherwise hand back to the agent.
    name = getattr(last_tool, "name", None)
    return "grade" if name in _RETRIEVAL_TOOLS else "agent"


def make_route_after_grade(max_retries: int):
    def route_after_grade(state: RagState) -> Literal["generate", "rewrite"]:
        if state.get("relevant"):
            return "generate"
        if state.get("retry_count", 0) < max_retries:
            return "rewrite"
        return "generate"  # exhausted retries → answer honestly from weak context
    return route_after_grade
```
> Note `route_after_tools` reads the last message's `.name` — a `ToolMessage` carries the tool name in LangChain. Verify `ToolMessage.name` is populated by `tools_node` (set `name=tc["name"]` on the `ToolMessage` if the installed version doesn't auto-fill it). Adjust during execution.

- [ ] **Step 3:** `uv run pytest tests/test_graph_nodes.py -q` → green; ruff + mypy clean.

---

### Task 11: Builder (wire the graph) + flow tests

**Files:** New `app/rag/graph/builder.py`; finalize `app/rag/graph/__init__.py`; Test `tests/test_graph_flow.py`

- [ ] **Step 1: Implement `app/rag/graph/builder.py`:**
```python
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.rag.embeddings import EmbeddingsProvider
from app.rag.graph.nodes import (
    make_nodes,
    make_route_after_grade,
    route_after_agent,
    route_after_tools,
)
from app.rag.graph.state import RagState
from app.rag.graph.tools import build_tools


def build_rag_graph(
    chat_model: BaseChatModel,
    embeddings: EmbeddingsProvider,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
):
    tools = build_tools(embeddings, sessionmaker, settings.retrieval_top_k)
    nodes = make_nodes(chat_model, tools, settings.chat_history_limit, settings.max_grade_retries)
    route_after_grade = make_route_after_grade(settings.max_grade_retries)

    g = StateGraph(RagState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    if settings.agentic_retrieval:
        # Agentic + corrective: the LLM decides whether/which tool to call.
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "generate": "generate"})
        g.add_conditional_edges("tools", route_after_tools, {"grade": "grade", "agent": "agent"})
    else:
        # Deterministic fallback for weak/local LLMs: always retrieve first (no tool decision).
        # A tiny seed node turns the question into a forced retrieve_notes tool call.
        from langchain_core.messages import AIMessage

        async def force_retrieve(state: RagState, config):
            call = {"name": "retrieve_notes", "args": {"query": state["question"]}, "id": "seed"}
            return {"messages": [AIMessage(content="", tool_calls=[call])]}

        g.add_node("force_retrieve", force_retrieve)
        g.add_edge(START, "force_retrieve")
        g.add_edge("force_retrieve", "tools")
        g.add_edge("tools", "grade")

    g.add_conditional_edges("grade", route_after_grade, {"generate": "generate", "rewrite": "rewrite"})
    g.add_edge("rewrite", "agent" if settings.agentic_retrieval else "force_retrieve")
    g.add_edge("generate", END)

    return g.compile(checkpointer=checkpointer)
```

- [ ] **Step 2: Finalize `app/rag/graph/__init__.py`** with both exports (see Task 7 Step 2).

- [ ] **Step 3: Flow tests** `tests/test_graph_flow.py` — compile with `FakeChatModel` + `InMemorySaver`, seed DB via fixtures, invoke end-to-end with a `config` carrying `thread_id`/`user_id`:
  - **Happy path:** queue [AIMessage(tool_call retrieve_notes), Grade(relevant=True), AIMessage("answer [1]")] → final message is the grounded answer; `state["context"]` populated.
  - **Corrective loop:** queue grades [False, then True] with a rewrite in between → asserts it rewrote once then answered; `retry_count == 1`.
  - **Retry cap:** queue always-False grades → at most `max_grade_retries` rewrites then generate (no infinite loop); assert final node is generate.
  - **Whole-doc path:** queue [AIMessage(tool_call get_document_content), AIMessage("summary")] → goes tools→agent→generate, **skips grade**.
  - **Direct answer:** queue [AIMessage("hello!")] (no tool_calls) → straight to generate.
  - **Linear fallback:** build with `agentic_retrieval=False` → retrieves without an agent tool-decision; queue [Grade(relevant=True), AIMessage("answer")].
  - **Multi-turn:** invoke twice on the same `thread_id`; second turn's state includes the first turn's messages (checkpointer persistence via `InMemorySaver`).

- [ ] **Step 4:** `uv run pytest tests/test_graph_flow.py -q` → green; full `make check`.

- [ ] **MILESTONE C — STOP. Suggested commit:**
```bash
git add -A && git commit -m "feat(phase3): LangGraph agentic+corrective graph (state, tools, nodes, builder) + flow tests"
```

---

## Milestone D — ChatService + checkpointer + lifespan wiring

### Task 12: ChatService

**Files:** New `app/services/chat.py`; Test `tests/test_chat_service.py`

- [ ] **Step 1: Failing tests** (compile a graph with `FakeChatModel` + `InMemorySaver`; build `ChatService` with the test `sessionmaker`):
  - `stream_answer` on a new conversation **creates a row** (title = truncated question), yields `meta` (with the new id) → `token`(s) → `citations` → `done`.
  - `stream_answer` with a bad `conversation_id` (another user's) raises `ConversationNotFound`.
  - second `stream_answer` on the returned id **reuses** the row and bumps `updated_at`.
  - `list_conversations` returns the user's rows newest-first.
  - `get_detail` returns messages read from the checkpointer state.
  - `delete_conversation` removes the row (and calls the checkpointer thread delete if available).

- [ ] **Step 2: Implement `app/services/chat.py`:**
```python
"""Chat orchestration: conversation lifecycle + streaming the graph as SSE events.

Uses its OWN async_sessionmaker (not the request session) because a StreamingResponse
keeps consuming this generator after the request's DB session would have closed.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.repositories.conversation import ConversationRepository

_TITLE_MAX = 120


class ConversationNotFound(Exception):
    pass


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _to_citations(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: c.get(k) for k in
         ("chunk_id", "document_id", "filename", "title", "page_number", "section", "score")}
        for c in context
    ]


class ChatService:
    def __init__(self, graph: Any, sessionmaker: async_sessionmaker) -> None:
        self._graph = graph
        self._sm = sessionmaker

    async def _create(self, user_id: uuid.UUID, question: str) -> uuid.UUID:
        async with self._sm() as s:
            convo = await ConversationRepository(s).create(
                user_id=user_id, title=question[:_TITLE_MAX]
            )
            await s.commit()
            return convo.id

    async def _ensure_owned(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        async with self._sm() as s:
            if await ConversationRepository(s).get_for_user(conversation_id, user_id) is None:
                raise ConversationNotFound(str(conversation_id))

    async def stream_answer(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        question: str,
        course: str | None = None,
        tags: list[str] | None = None,
        top_k: int | None = None,
    ) -> AsyncIterator[str]:
        if conversation_id is None:
            conversation_id = await self._create(user_id, question)
        else:
            await self._ensure_owned(conversation_id, user_id)

        yield _sse("meta", {"conversation_id": str(conversation_id)})

        config = {"configurable": {
            "thread_id": str(conversation_id),
            "user_id": user_id,
            "course": course, "tags": tags, "top_k": top_k,
        }}
        inputs = {"messages": [HumanMessage(question)], "question": question, "retry_count": 0}

        try:
            async for msg, metadata in self._graph.astream(inputs, config, stream_mode="messages"):
                if metadata.get("langgraph_node") == "generate" and getattr(msg, "content", ""):
                    yield _sse("token", {"delta": msg.content})
            state = await self._graph.aget_state(config)
            yield _sse("citations", _to_citations(state.values.get("context", [])))
            async with self._sm() as s:
                await ConversationRepository(s).touch(conversation_id)
                await s.commit()
            yield _sse("done", {})
        except Exception as exc:  # surface a clean error frame, then end the stream
            yield _sse("error", {"detail": str(exc)})

    async def list_conversations(self, user_id: uuid.UUID) -> list[Any]:
        async with self._sm() as s:
            return await ConversationRepository(s).list_for_user(user_id)

    async def get_detail(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
        async with self._sm() as s:
            convo = await ConversationRepository(s).get_for_user(conversation_id, user_id)
        if convo is None:
            raise ConversationNotFound(str(conversation_id))
        state = await self._graph.aget_state(
            {"configurable": {"thread_id": str(conversation_id)}}
        )
        messages = []
        for m in state.values.get("messages", []):
            role = "assistant" if isinstance(m, AIMessage) else (
                "user" if isinstance(m, HumanMessage) else None)
            if role and getattr(m, "content", ""):
                messages.append({"role": role, "content": m.content})
        return {"conversation": convo, "messages": messages}

    async def delete_conversation(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._ensure_owned(conversation_id, user_id)
        async with self._sm() as s:
            await ConversationRepository(s).delete(conversation_id)
            await s.commit()
        # Best-effort checkpointer cleanup (method name/availability is version-dependent).
        deleter = getattr(self._graph.checkpointer, "adelete_thread", None)
        if deleter is not None:
            await deleter(str(conversation_id))
```
> `get_detail` history omits per-message citations (we persist only the latest `context`). That matches the spec (live stream carries citations; history is role+content). Note it in the learning doc.

- [ ] **Step 2b:** Tests assert SSE frames by parsing the yielded strings. `uv run pytest tests/test_chat_service.py -q` → green; ruff + mypy clean.

---

### Task 13: Lifespan wiring + deps

**Files:** Modify `app/main.py`, `app/api/deps.py`

- [ ] **Step 1: Lifespan** in `app/main.py` — open a psycopg3 pool, build the checkpointer (`setup()` once), build the chat model + embeddings, compile the graph, stash on `app.state`. **Mind the psycopg gotchas** (autocommit + no prepared statements + dict rows) or `AsyncPostgresSaver` misbehaves:
```python
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.rag.embeddings import GeminiEmbeddingsProvider  # match the real provider name/ctor
from app.rag.graph import build_rag_graph
from app.rag.llm import build_chat_model


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    configure_logging()
    settings = get_settings()

    pool = AsyncConnectionPool(
        conninfo=settings.checkpointer_conninfo,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # idempotent; creates checkpoint tables if missing

    chat_model = build_chat_model(settings)
    embeddings = GeminiEmbeddingsProvider(settings)  # match real ctor (see app/api/deps.get_embeddings)
    app.state.chat_graph = build_rag_graph(
        chat_model, embeddings, get_sessionmaker(), settings, checkpointer
    )

    try:
        yield
    finally:
        await pool.close()
        await get_engine().dispose()
```
> Read the real `GeminiEmbeddingsProvider` constructor (via `get_embeddings` in `deps.py`) and call it exactly the same way. Keep the existing `configure_logging()` call.

- [ ] **Step 2: Deps** in `app/api/deps.py`:
```python
from starlette.requests import Request
from app.services.chat import ChatService

def get_chat_graph(request: Request):  # compiled once in lifespan
    return request.app.state.chat_graph

def get_chat_service(request: Request) -> ChatService:
    return ChatService(request.app.state.chat_graph, get_sessionmaker())
```
> Import `get_sessionmaker` from `app.db.session` (already used by `get_db`).

- [ ] **Step 3:** ruff + mypy clean. (End-to-end exercised in Milestone E.)

- [ ] **MILESTONE D — STOP. Suggested commit:**
```bash
git add -A && git commit -m "feat(phase3): ChatService + checkpointer pool + graph lifespan wiring"
```

---

## Milestone E — API layer

### Task 14: Schemas

**Files:** New `app/schemas/chat.py`

- [ ] **Implement** (match `app/schemas/document.py` style; `top_k` bounded like `SearchRequest`):
```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    course: str | None = None
    tags: list[str] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


class Citation(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str | None = None
    title: str | None = None
    page_number: int | None = None
    section: str | None = None
    score: float | None = None


class ConversationResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    role: str
    content: str


class ConversationDetail(ConversationResponse):
    messages: list[MessageResponse]
```

---

### Task 15: Chat endpoint (SSE)

**Files:** New `app/api/chat.py`; Modify `app/main.py` (register); Test `tests/test_chat_api.py`

- [ ] **Step 1: Implement `app/api/chat.py`:**
```python
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_chat_service, get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("")
async def chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> StreamingResponse:
    stream = service.stream_answer(
        user_id=current_user.id,
        conversation_id=body.conversation_id,
        question=body.question,
        course=body.course,
        tags=body.tags,
        top_k=body.top_k,
    )
    return StreamingResponse(stream, media_type="text/event-stream")
```
> Ownership errors are surfaced as an SSE `error` frame inside the stream (see `ChatService`), not an HTTP status, because the response has already begun streaming. The `404` contract in the spec applies to the conversations endpoints (Task 16). If you prefer a pre-stream `404` for a bad `conversation_id`, do the ownership check in the endpoint *before* returning `StreamingResponse` and raise `HTTPException(404)` there — acceptable refinement.

- [ ] **Step 2: Register** in `app/main.py`: `app.include_router(chat.router)`.

- [ ] **Step 3: Integration tests `tests/test_chat_api.py`** — override `get_chat_service` to use a `FakeChatModel`-backed graph + `InMemorySaver` + the test sessionmaker (add a `client`-level override, mirroring how `get_embeddings` is overridden in conftest). Assert:
  - `POST /chat` returns `200` with `content-type: text/event-stream`; body contains `event: meta`, `event: token`, `event: citations`, `event: done`.
  - the response includes a `conversation_id`; a second `POST /chat` with that id continues the thread.
  - `401` without a token.
  - user-isolation: posting another user's `conversation_id` yields an `error` frame (or `404` if you took the pre-stream-check refinement).

- [ ] **Step 4:** `uv run pytest tests/test_chat_api.py -q` → green; ruff + mypy clean.

---

### Task 16: Conversations endpoints

**Files:** New `app/api/conversations.py`; Modify `app/main.py`; Test `tests/test_conversations_api.py`

- [ ] **Step 1: Implement `app/api/conversations.py`** — list/detail/delete, mapping `ConversationNotFound` → `404`:
```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_chat_service, get_current_user
from app.models.user import User
from app.schemas.chat import ConversationDetail, ConversationResponse
from app.services.chat import ChatService, ConversationNotFound

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> list[ConversationResponse]:
    return await service.list_conversations(current_user.id)  # type: ignore[return-value]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> ConversationDetail:
    try:
        data = await service.get_detail(conversation_id, current_user.id)
    except ConversationNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    convo = data["conversation"]
    return ConversationDetail(
        id=convo.id, title=convo.title, created_at=convo.created_at,
        updated_at=convo.updated_at, messages=data["messages"],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> None:
    try:
        await service.delete_conversation(conversation_id, current_user.id)
    except ConversationNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
```

- [ ] **Step 2: Register** `app.include_router(conversations.router)`.

- [ ] **Step 3: Tests `tests/test_conversations_api.py`** — after a `POST /chat`, `GET /conversations` lists it; `GET /conversations/{id}` returns message history; `DELETE` → `204` then `GET` → `404`; user-isolation (B can't see/get/delete A's); `401` without token.

- [ ] **Step 4:** `uv run pytest tests/test_conversations_api.py -q` → green; ruff + mypy clean.

---

### Task 17: Real-checkpointer integration test

**Files:** Test `tests/test_checkpointer_integration.py`

- [ ] **Step 1:** Build a graph with a real `AsyncPostgresSaver` pointed at `notes_rag_test` (reuse the test conninfo; `await saver.setup()`), `FakeChatModel`, fake embeddings, test sessionmaker. Run two turns on one `thread_id`; assert the **second turn's state contains the first turn's messages** (true persistence, not just in-memory). Clean up the checkpoint thread at the end if `adelete_thread` exists.
> This is the one test that exercises the Postgres checkpointer. Keep it isolated (own thread_id) and tolerant of the checkpointer's own tables coexisting in the test DB. If conftest's truncate list must include checkpoint tables, the executor may ONLY add them to the truncate list after confirming `setup()` created them — and must flag this test-infra change for controller review per the standing rule.

- [ ] **Step 2:** `make check` (full suite) → all green.

- [ ] **MILESTONE E — STOP. Suggested commit:**
```bash
git add -A && git commit -m "feat(phase3): chat (SSE) + conversations API, schemas, checkpointer integration test"
```

---

## Milestone F — Docker / CI / learning doc / final verification

### Task 18: Docker & compose & CI

**Files:** Modify `docker-compose.yml`, `backend/Dockerfile` (likely no change), `.github/workflows/ci.yml`

- [x] **Step 1:** No new **system** deps (psycopg[binary] ships its own libpq). Confirm the backend service has `GOOGLE_API_KEY` passthrough (already added in Phase 2) and add `ANTHROPIC_API_KEY` passthrough (optional, empty default). The checkpointer reuses the **same** Postgres `DATABASE_URL` — no new service/volume.
- [x] **Step 2:** CI: the new deps install via `uv sync`; tests need no API key (all faked) but DO need the Postgres service (already present from Phase 2). Confirm the checkpointer integration test runs against the CI Postgres. No OCR/key changes needed.
- [ ] **Step 3:** `docker compose build backend` succeeds; `make up` boots and `GET /health` is green (lifespan runs `setup()` against the compose Postgres).

### Task 19: Learning doc

**Files:** New `docs/learning/03-agentic-rag.md`

- [x] **Write** a beginner-friendly explainer (match `02-ingestion-retrieval.md` tone) covering: what LangGraph is (nodes/edges/state), the combined agentic+corrective graph (with the ASCII flow), the 3 tools and why only 3, the configurable LLM factory (and why a factory not an ABC; how to switch to Claude/local), SSE streaming, the Postgres checkpointer (thread_id = conversation id; two DB drivers; schema outside Alembic), and the anti-hallucination grounding contract. Note history omits per-message citations.

### Task 20: Final verification

- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q` → all green.
- [x] Update `README.md` roadmap row: Phase 3 → **done**; update the statuses (they're stale — Phase 0 still says "Current").
- [ ] **MILESTONE F — STOP. Suggested commit:**
```bash
git add -A && git commit -m "feat(phase3): docker/CI passthrough, learning doc, README roadmap, final verification"
```

---

## Risks / execution notes (carry-over from spec §10)

- **Streaming chunk format** (`astream(stream_mode="messages")`): standard is `async for msg, metadata in ...`. Some versions emit a unified StreamPart (`chunk["type"]=="messages"`, `chunk["data"]=(msg, metadata)`). The executor must confirm the installed shape with a one-line smoke test and adapt `ChatService.stream_answer` accordingly.
- **psycopg pool gotchas:** `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row` are required for `AsyncPostgresSaver`. Open the pool with `open=False` + `await pool.open()` to avoid the constructor-open deprecation.
- **Tool `config` injection:** confirm `config: RunnableConfig` is auto-injected and hidden from the LLM schema in the installed langchain-core; if not, switch to `InjectedToolArg`/`get_runnable_config()`.
- **`ToolMessage.name`:** set `name=tc["name"]` on the `ToolMessage` if the version doesn't auto-populate it (the `route_after_tools` router reads it).
- **`with_structured_output` on the real model:** Gemini supports it; a weak local model may not — that's the `AGENTIC_RETRIEVAL=false` / linear-path escape hatch, but grading still needs structured output. Document that grading quality is model-dependent.
- **Session lifetime in streaming:** ChatService deliberately uses `sessionmaker`, never the request session, so DB writes survive the `StreamingResponse`.
- **No auto-commit; stop at every milestone.** Subagents must not touch test infra (conftest/CI/Docker) beyond what a task authorizes, and must flag the checkpoint-tables truncate change in Task 17 for controller review.
