# Ingestion Quality & Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild ingestion so chunks respect document structure (with a semantic fallback where none exists), re-uploading an edited document only re-embeds what changed, and none of this blocks the HTTP request.

**Architecture:** Three milestones, each shippable independently: (1) move ingestion onto a Postgres-backed background job queue (`procrastinate`) with a visible processing status; (2) rewrite the chunker into a structure-aware-primary/semantic-fallback/fixed-size-backstop cascade, including a PDF parser rewrite; (3) add a "Replace" flow that diffs chunk-by-chunk against the previous version so unchanged content is never re-embedded.

**Tech Stack:** FastAPI, SQLAlchemy (async) + Alembic, pgvector, `procrastinate` (Postgres-backed task queue), `fastembed` (ONNX local embeddings for semantic chunking), `pymupdf4llm` (PDF → Markdown with heading detection), existing `langchain-text-splitters`/`tiktoken`.

Spec: `docs/superpowers/specs/2026-07-26-ingestion-quality-design.md`

## Global Constraints

- Do NOT commit automatically — the user commits at milestone boundaries (per their standing preference). Each task still ends with a `git add` + `git commit` step for the worker to run locally; whether that's later squashed/held is the user's call, not this plan's.
- All new async ingestion code must remain testable without a real running worker process — use direct task-function invocation and dependency overrides, the same pattern already used for `get_current_user` elsewhere in this codebase.
- No version history / rollback (Replace is overwrite-only). No cross-document chunk reuse. No fuzzy/near-duplicate matching — only exact content-hash reuse.
- Follow existing code conventions exactly: typed Python (mypy strict-ish per `pyproject.toml`), `ruff` clean, async SQLAlchemy 2.0 style, dependency-injection via `app/api/deps.py`, tests via `pytest-asyncio` with the existing `db_session` fixture and `tests/fakes.py` fakes.
- One known uncertainty, flagged honestly: **`pymupdf4llm`'s exact API (page-chunking parameters) should be verified against the installed version's actual signature during Task 11** — the code below reflects its documented behavior as of this plan's writing, but the implementer must run it against a real sample PDF and adjust if the installed version differs.

---

## Milestone 1: Async ingestion via `procrastinate`

Moves the existing synchronous `IngestionService.ingest()` into a fast `stage()` (runs inline in the request) + heavy `process()` (runs in a background job), adds a `status` field so the frontend can show progress, and wires up `procrastinate` as the job queue — all *before* touching chunking logic, so this milestone ships independently and everything in Milestones 2–3 automatically runs through it.

### Task 1: Add `status`/`error_message` to `Document`

**Files:**
- Modify: `backend/app/models/document.py`
- Create: `backend/app/db/migrations/versions/0005_document_status.py`
- Test: `backend/tests/test_document_models.py`

**Interfaces:**
- Produces: `Document.status: str` (values used throughout this plan: `"pending"`, `"processing"`, `"ready"`, `"failed"`), `Document.error_message: str | None`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_document_models.py (append)
import uuid

import pytest
from app.models.document import Document


@pytest.mark.asyncio
async def test_document_defaults_to_pending_status_with_no_error(db_session):
    doc = Document(
        user_id=uuid.uuid4(),
        filename="a.txt",
        content_type="text/plain",
        content_hash="a" * 64,
        storage_path="/tmp/a.txt",
        file_size=1,
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )
    db_session.add(doc)
    await db_session.flush()
    assert doc.status == "pending"
    assert doc.error_message is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_document_models.py::test_document_defaults_to_pending_status_with_no_error -v`
Expected: FAIL — `TypeError` or `AttributeError` (`status` isn't a real column/kwarg yet).

- [ ] **Step 3: Add the columns to the model**

```python
# backend/app/models/document.py — add inside class Document, after chunk_count:
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(`Text` is already imported in this file for `DocumentChunk.content`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_document_models.py::test_document_defaults_to_pending_status_with_no_error -v`
Expected: PASS

- [ ] **Step 5: Write the migration**

```python
# backend/app/db/migrations/versions/0005_document_status.py
"""add status/error_message to documents

Revision ID: 0005_document_status
Revises: 0004_conversations
Create Date: 2026-07-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_document_status"
down_revision = "0004_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
    )
    op.add_column("documents", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "error_message")
    op.drop_column("documents", "status")
```

Note: the migration backfills existing rows to `"ready"` (they were already fully processed under the old synchronous flow), while the Python model's default for *new* rows going through `stage()` is `"pending"` — that default only applies at the ORM/application layer, not retroactively to existing rows, which is exactly the desired behavior here.

- [ ] **Step 6: Apply the migration against the test DB and run the full model test file**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/test_document_models.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/document.py backend/app/db/migrations/versions/0005_document_status.py backend/tests/test_document_models.py
git commit -m "feat(ingestion): add status/error_message fields to Document"
```

### Task 2: `StorageBackend.read()`

Background processing re-reads the saved file from disk (a job argument can't safely carry raw file bytes through the Postgres-backed queue), so `StorageBackend` needs a read primitive alongside `save`/`delete`.

**Files:**
- Modify: `backend/app/rag/storage.py`
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- Produces: `StorageBackend.read(path: str) -> bytes`, implemented on `LocalFileStorage`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_storage.py (append)
def test_read_returns_saved_bytes(tmp_path: Path):
    storage = LocalFileStorage(str(tmp_path))
    path = storage.save(uuid.uuid4(), "notes.txt", b"hello world")
    assert storage.read(path) == b"hello world"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_storage.py::test_read_returns_saved_bytes -v`
Expected: FAIL with `AttributeError: 'LocalFileStorage' object has no attribute 'read'`

- [ ] **Step 3: Implement**

```python
# backend/app/rag/storage.py — add to StorageBackend (abstract) and LocalFileStorage:

class StorageBackend(ABC):
    @abstractmethod
    def save(self, user_id: uuid.UUID, filename: str, data: bytes) -> str: ...

    @abstractmethod
    def read(self, path: str) -> bytes: ...

    @abstractmethod
    def delete(self, path: str) -> None: ...


class LocalFileStorage(StorageBackend):
    # ... existing save() unchanged ...

    def read(self, path: str) -> bytes:
        return Path(path).read_bytes()

    # ... existing delete() unchanged ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_storage.py -v`
Expected: PASS (all tests in file)

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/storage.py backend/tests/test_storage.py
git commit -m "feat(ingestion): add StorageBackend.read for background processing"
```

### Task 3: Split `IngestionService.ingest()` into `stage()` + `process()`

**Files:**
- Modify: `backend/app/services/ingestion.py`
- Modify: `backend/app/db/repositories/document.py`
- Modify: `backend/tests/test_ingestion_service.py` (existing `ingest()`-based tests are replaced, not kept — `ingest()` is being removed)

**Interfaces:**
- Consumes: `StorageBackend.read` (Task 2), `Document.status`/`error_message` (Task 1).
- Produces: `IngestionService.stage(...) -> Document` (fast path, no chunks yet, `status="pending"`), `IngestionService.process(document_id: uuid.UUID) -> None` (heavy path, called by the background job in Task 4). `DocumentRepository.set_status(document_id, status, error_message=None) -> None` and `DocumentRepository.update_after_processing(document_id, *, page_count, chunk_count, status) -> None`.

- [ ] **Step 1: Write the failing tests (replacing the file's `ingest()`-based tests)**

```python
# backend/tests/test_ingestion_service.py — REPLACE THE WHOLE FILE with:
import hashlib
import uuid
from pathlib import Path

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.rag.chunking import Chunker
from app.rag.parsing import ParserDispatcher
from app.rag.storage import LocalFileStorage
from app.services.ingestion import DuplicateDocument, IngestionService

from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider


async def _user(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


def _service(db_session, storage, embeddings=None):
    return IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=30, chunk_overlap_tokens=5),
        embeddings=embeddings or FakeEmbeddingsProvider(),
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )


@pytest.mark.asyncio
async def test_stage_creates_pending_document_without_chunks(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)

    doc = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"some text", title="T", course="BIO", tags=["x"],
    )

    assert doc.status == "pending"
    assert doc.chunk_count == 0
    assert doc.page_count is None
    assert Path(doc.storage_path).exists()  # noqa: ASYNC240
    assert await ChunkRepository(db_session).list() == []


@pytest.mark.asyncio
async def test_stage_duplicate_raises_and_writes_no_file(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    data = b"same bytes"
    await svc.stage(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)

    before = len(await DocumentRepository(db_session).list_for_user(user.id))
    with pytest.raises(DuplicateDocument):
        await svc.stage(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)
    after = len(await DocumentRepository(db_session).list_for_user(user.id))
    assert before == after == 1


@pytest.mark.asyncio
async def test_process_parses_chunks_embeds_and_marks_ready(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"first paragraph of notes. second paragraph of notes.",
    )

    await svc.process(staged.id)

    processed = await DocumentRepository(db_session).get(staged.id)
    assert processed.status == "ready"
    assert processed.chunk_count >= 1
    chunks = await ChunkRepository(db_session).list()
    assert len(chunks) == processed.chunk_count
    assert all(len(c.embedding) == 1536 for c in chunks)


@pytest.mark.asyncio
async def test_process_marks_failed_and_reraises_on_embedding_error(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    svc = _service(db_session, storage, embeddings=BoomEmbeddings())
    staged = await svc.stage(
        user_id=user.id, filename="x.txt", content_type="text/plain", data=b"some text here"
    )

    with pytest.raises(RuntimeError):
        await svc.process(staged.id)

    failed = await DocumentRepository(db_session).get(staged.id)
    assert failed.status == "failed"
    assert failed.error_message == "embedding API down"
    # The staged file itself is NOT deleted on a process() failure (unlike the old
    # ingest()'s cleanup) — the document row still exists and is retryable.
    assert Path(failed.storage_path).exists()  # noqa: ASYNC240
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_ingestion_service.py -v`
Expected: FAIL — `AttributeError: 'IngestionService' object has no attribute 'stage'`

- [ ] **Step 3: Add the repository helpers**

```python
# backend/app/db/repositories/document.py — add to DocumentRepository:
    async def set_status(
        self, document_id: uuid.UUID, status: str, error_message: str | None = None
    ) -> None:
        doc = await self.get(document_id)
        if doc is None:
            return  # deleted concurrently; nothing to update
        doc.status = status
        doc.error_message = error_message
        await self._session.flush()

    async def update_after_processing(
        self, document_id: uuid.UUID, *, page_count: int | None, chunk_count: int, status: str
    ) -> None:
        doc = await self.get(document_id)
        if doc is None:
            return
        doc.page_count = page_count
        doc.chunk_count = chunk_count
        doc.status = status
        await self._session.flush()
```

- [ ] **Step 4: Replace `ingest()` with `stage()` + `process()`**

```python
# backend/app/services/ingestion.py — full replacement of the IngestionService body
# (keep IngestionError/DuplicateDocument and __init__ unchanged; replace only `ingest`):

    async def stage(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        content_type: str,
        data: bytes,
        title: str | None = None,
        course: str | None = None,
        tags: list[str] | None = None,
    ) -> Document:
        """Fast, synchronous half of ingestion — safe to call inline in the request.
        Dedup-checks, saves the raw file, and creates a 'pending' Document row with
        no chunks yet. The heavy work happens later in process(), off the request."""
        content_hash = hashlib.sha256(data).hexdigest()
        existing = await self._documents.get_by_user_and_hash(user_id, content_hash)
        if existing is not None:
            raise DuplicateDocument(existing)

        storage_path = self._storage.save(user_id, filename, data)
        try:
            document = await self._documents.create(
                user_id=user_id,
                filename=filename,
                title=title,
                course=course,
                tags=tags or [],
                content_type=content_type,
                content_hash=content_hash,
                storage_path=storage_path,
                file_size=len(data),
                page_count=None,
                chunk_count=0,
                embedding_model=self._embedding_model,
                embedding_dimension=self._embedding_dimension,
                status="pending",
            )
            await self._session.commit()
        except Exception:
            self._storage.delete(storage_path)
            raise
        return document

    async def process(self, document_id: uuid.UUID) -> None:
        """Heavy half of ingestion, run by the background worker: parse -> chunk ->
        embed -> persist chunks -> mark ready. On any failure, marks the document
        'failed' with the error message (rather than deleting it — it stays
        retryable) and re-raises so the job queue also records the failure."""
        document = await self._documents.get(document_id)
        if document is None:
            return  # deleted before processing started

        await self._documents.set_status(document.id, "processing")
        await self._session.commit()

        try:
            data = self._storage.read(document.storage_path)
            parsed = self._parser.parse(data, document.content_type)
            chunks = self._chunker.split(parsed)
            vectors = self._embeddings.embed_documents([c.content for c in chunks])

            async with self._session.begin_nested():
                await self._chunks.add_many(
                    [
                        dict(
                            document_id=document.id,
                            user_id=document.user_id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            token_count=chunk.token_count,
                            page_number=chunk.page_number,
                            section=chunk.section,
                            embedding=vector,
                        )
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ]
                )
                await self._documents.update_after_processing(
                    document.id,
                    page_count=parsed.page_count,
                    chunk_count=len(chunks),
                    status="ready",
                )
            await self._session.commit()
        except Exception as exc:
            await self._session.rollback()
            await self._documents.set_status(document.id, "failed", error_message=str(exc))
            await self._session.commit()
            raise
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_ingestion_service.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 6: Run the full backend test suite to check nothing else references the removed `ingest()`**

Run: `cd backend && uv run pytest -v`
Expected: Failures only in `test_documents_api.py` (still calling the old flow through the API — fixed in Task 5). No other unexpected failures.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/ingestion.py backend/app/db/repositories/document.py backend/tests/test_ingestion_service.py
git commit -m "refactor(ingestion): split IngestionService.ingest into stage()+process()"
```

### Task 4: `procrastinate` app, background task, and testable enqueue seam

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/jobs/__init__.py`
- Create: `backend/app/jobs/app.py`
- Create: `backend/app/jobs/ingestion_tasks.py`
- Modify: `backend/app/api/deps.py`
- Test: `backend/tests/test_ingestion_tasks.py`

**Interfaces:**
- Consumes: `IngestionService.process` (Task 3), `Settings.checkpointer_conninfo` (existing, `backend/app/core/config.py:78-80`).
- Produces: `app.jobs.app.app` (the `procrastinate.App` singleton), `app.jobs.ingestion_tasks.process_document` (a `procrastinate` task, `name="process_document"`), `app.api.deps.enqueue_document_processing(document_id: uuid.UUID) -> None`, `app.api.deps.get_enqueue_processing()` (a FastAPI dependency returning that callable — overridable in tests, matching this codebase's existing DI pattern).

- [ ] **Step 1: Add the dependency**

```toml
# backend/pyproject.toml — add to [project] dependencies:
    "procrastinate[psycopg]>=2.14",
```

Run: `cd backend && uv sync`

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_ingestion_tasks.py
import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_process_document_task_delegates_to_ingestion_service_process():
    from app.jobs import ingestion_tasks

    document_id = uuid.uuid4()
    fake_process = AsyncMock()

    with patch("app.jobs.ingestion_tasks.IngestionService") as FakeService:
        FakeService.return_value.process = fake_process
        await ingestion_tasks.process_document(document_id=str(document_id))

    fake_process.assert_awaited_once_with(document_id)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ingestion_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.jobs'`

- [ ] **Step 4: Create the `procrastinate` app**

```python
# backend/app/jobs/__init__.py
```
(empty — just marks the package)

```python
# backend/app/jobs/app.py
from procrastinate import App, PsycopgConnector

from app.core.config import get_settings


def build_app() -> App:
    settings = get_settings()
    # Reuses the same stripped (non-asyncpg) conninfo already used for the LangGraph
    # checkpointer (backend/app/core/config.py:78-80) — procrastinate's psycopg
    # connector takes a plain postgres:// DSN, not the asyncpg-prefixed SQLAlchemy one.
    connector = PsycopgConnector(conninfo=settings.checkpointer_conninfo)
    return App(connector=connector)


app = build_app()
```

- [ ] **Step 5: Create the ingestion task**

```python
# backend/app/jobs/ingestion_tasks.py
import uuid

from app.core.config import get_settings
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.session import get_sessionmaker
from app.jobs.app import app
from app.rag.chunking import Chunker
from app.rag.embeddings import GeminiEmbeddingsProvider
from app.rag.ocr import TesseractOcr
from app.rag.parsing import ParserDispatcher
from app.rag.storage import LocalFileStorage
from app.services.ingestion import IngestionService


@app.task(name="process_document")
async def process_document(document_id: str) -> None:
    """Background job body: builds a real IngestionService (same adapters the API
    uses) against its own DB session, and delegates the heavy work to process().
    document_id is passed as str (job arguments must be JSON-serializable)."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        ocr = TesseractOcr(language=settings.ocr_language, cmd=settings.tesseract_cmd)
        service = IngestionService(
            session=session,
            documents=DocumentRepository(session),
            chunks=ChunkRepository(session),
            storage=LocalFileStorage(settings.upload_dir),
            parser=ParserDispatcher(
                ocr=ocr,
                ocr_enabled=settings.ocr_enabled,
                min_chars=settings.pdf_ocr_min_chars_per_page,
            ),
            chunker=Chunker(
                chunk_tokens=settings.chunk_tokens, chunk_overlap_tokens=settings.chunk_overlap_tokens
            ),
            embeddings=GeminiEmbeddingsProvider(settings),
            embedding_model=settings.embedding_model,
            embedding_dimension=settings.embedding_dimension,
        )
        await service.process(uuid.UUID(document_id))
```

- [ ] **Step 6: Add the testable enqueue seam to `deps.py`**

```python
# backend/app/api/deps.py — add imports and these two functions:
from collections.abc import Awaitable, Callable
from app.jobs.ingestion_tasks import process_document


async def enqueue_document_processing(document_id: uuid.UUID) -> None:
    await process_document.defer_async(document_id=str(document_id))


def get_enqueue_processing() -> Callable[[uuid.UUID], Awaitable[None]]:
    """FastAPI dependency wrapper so tests can override the real enqueue call
    with a no-op/recording fake, the same pattern used for get_current_user."""
    return enqueue_document_processing
```

(`import uuid` — check the top of `deps.py`; add it if not already present.)

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_ingestion_tasks.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/jobs backend/app/api/deps.py backend/tests/test_ingestion_tasks.py
git commit -m "feat(ingestion): add procrastinate background job for document processing"
```

### Task 5: Wire the API endpoint to the staged flow

**Files:**
- Modify: `backend/app/api/documents.py`
- Modify: `backend/app/schemas/document.py`
- Modify: `backend/tests/test_documents_api.py`

**Interfaces:**
- Consumes: `IngestionService.stage` (Task 3), `get_enqueue_processing` (Task 4).
- Produces: `DocumentResponse.status: str`, `DocumentResponse.error_message: str | None`.

- [ ] **Step 1: Update the response schema**

```python
# backend/app/schemas/document.py — add to DocumentResponse, after chunk_count:
    status: str
    error_message: str | None
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_documents_api.py — add near the existing upload tests:
@pytest.mark.asyncio
async def test_upload_returns_pending_status_and_enqueues_processing(
    client, auth_headers, monkeypatch
):
    from app.api import deps

    enqueued: list[str] = []

    async def fake_enqueue(document_id):
        enqueued.append(str(document_id))

    app_.dependency_overrides[deps.get_enqueue_processing] = lambda: fake_enqueue

    r = await client.post(
        "/documents",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
        headers=auth_headers,
    )

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["chunk_count"] == 0
    assert enqueued == [body["id"]]

    app_.dependency_overrides.pop(deps.get_enqueue_processing, None)
```

Check the top of `test_documents_api.py` for how the FastAPI `app` instance and `auth_headers`/`client` fixtures are already imported/named (likely `from app.main import app as app_` or similar, and a `client`/`auth_headers` fixture from `conftest.py`) — match the existing import alias exactly rather than introducing a second name for the same object.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_documents_api.py::test_upload_returns_pending_status_and_enqueues_processing -v`
Expected: FAIL — endpoint still calls the removed `ingest()`, or status field missing from response.

- [ ] **Step 4: Update the upload endpoint**

```python
# backend/app/api/documents.py — replace the upload_document body:
from collections.abc import Awaitable, Callable
import uuid

from app.api.deps import get_current_user, get_enqueue_processing, get_ingestion_service

# ... (keep other imports as-is) ...

@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": DuplicateDocumentResponse}},
)
async def upload_document(
    file: UploadFile = File(...),  # noqa: B008
    title: str | None = Form(default=None),  # noqa: B008
    course: str | None = Form(default=None),  # noqa: B008
    tags: list[str] | None = Form(default=None),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: IngestionService = Depends(get_ingestion_service),  # noqa: B008
    enqueue: Callable[[uuid.UUID], Awaitable[None]] = Depends(get_enqueue_processing),  # noqa: B008
) -> DocumentResponse | JSONResponse:
    settings = get_settings()
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")

    content_type = sniff_content_type(file.filename or "", data)
    if content_type is None or content_type not in settings.allowed_content_types:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file type")

    try:
        document = await service.stage(
            user_id=current_user.id,
            filename=sanitize_filename(file.filename or "upload"),
            content_type=content_type,
            data=data,
            title=title,
            course=course,
            tags=tags,
        )
    except DuplicateDocument as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Document already exists", "document_id": str(exc.existing.id)},
        )
    await enqueue(document.id)
    return DocumentResponse.model_validate(document)
```

- [ ] **Step 5: Run test to verify it passes, then run the full API test file**

Run: `cd backend && uv run pytest tests/test_documents_api.py -v`
Expected: PASS (all tests — the other pre-existing upload/list/delete tests should be unaffected since `DocumentResponse` gained fields, not lost any).

- [ ] **Step 6: Regenerate the frontend's OpenAPI types**

Run: `make dev` (backend running) then `cd frontend && npm run gen:api`
Expected: `frontend/src/api/schema.ts` picks up `status`/`error_message` on `DocumentResponse`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/documents.py backend/app/schemas/document.py backend/tests/test_documents_api.py frontend/src/api/schema.ts
git commit -m "feat(ingestion): upload endpoint stages+enqueues instead of processing inline"
```

### Task 6: Frontend status polling + badge

**Files:**
- Modify: `frontend/src/api/hooks/useDocuments.ts`
- Modify: `frontend/src/components/documents/DocumentRow.tsx`
- Modify: `frontend/tests/documents.test.tsx`

**Interfaces:**
- Consumes: `DocumentResponse.status`/`error_message` (Task 5, now in the generated schema).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/documents.test.tsx — add a case (adjust imports/mocks to match this
// file's existing MSW setup for GET /documents):
it("shows a processing badge for a pending document and a failed badge with the error for a failed one", async () => {
  server.use(
    http.get("*/documents", () =>
      HttpResponse.json([
        { id: "1", filename: "a.pdf", status: "pending", error_message: null, /* ...other required fields... */ },
        { id: "2", filename: "b.pdf", status: "failed", error_message: "Embedding API down", /* ... */ },
      ]),
    ),
  )
  render(<DocumentList />, { wrapper: Providers })

  expect(await screen.findByText(/processing/i)).toBeInTheDocument()
  expect(await screen.findByText(/failed/i)).toBeInTheDocument()
  expect(await screen.findByText(/embedding api down/i)).toBeInTheDocument()
})
```

Match the exact MSW handler setup, `Providers` wrapper name, and full `DocumentResponse` fixture shape already used elsewhere in this test file — don't guess field names not already established there.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- documents.test.tsx`
Expected: FAIL — no "processing"/"failed" text rendered yet.

- [ ] **Step 3: Add a `refetchInterval` while any document isn't ready**

```typescript
// frontend/src/api/hooks/useDocuments.ts — replace useDocuments's body:
export function useDocuments(course?: string): UseQueryResult<DocumentResponse[], unknown> {
  return $api.useQuery("get", "/documents", {
    params: { query: { course } },
  }, {
    // Keep polling while anything is still processing, so the list flips to
    // ready/failed on its own without the user manually refreshing. No interval
    // once everything has settled (pending/processing gone) — avoids polling forever.
    refetchInterval: (query) => {
      const docs = query.state.data
      const stillWorking = docs?.some((d) => d.status === "pending" || d.status === "processing")
      return stillWorking ? 2000 : false
    },
  })
}
```

- [ ] **Step 4: Add the status badge to `DocumentRow`**

```tsx
// frontend/src/components/documents/DocumentRow.tsx — add near the filename/title display:
{document.status !== "ready" && (
  <span
    className={
      document.status === "failed"
        ? "text-sm text-destructive"
        : "text-sm text-muted-foreground"
    }
  >
    {document.status === "failed" ? `Failed: ${document.error_message ?? "Unknown error"}` : "Processing…"}
  </span>
)}
```

Adjust the exact JSX placement/className conventions to match the rest of `DocumentRow.tsx` (existing badge/text patterns for other states like the delete-confirm UI) rather than introducing a new ad hoc style.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm run test -- documents.test.tsx`
Expected: PASS

- [ ] **Step 6: Run the full frontend test suite, typecheck, lint**

Run: `cd frontend && npm run test && npm run typecheck && npm run lint`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/hooks/useDocuments.ts frontend/src/components/documents/DocumentRow.tsx frontend/tests/documents.test.tsx
git commit -m "feat(ingestion): poll and show processing/failed status in the document list"
```

**Milestone 1 checkpoint:** Ingestion is now non-blocking end-to-end. Run `make dev` (backend) + `cd frontend && npm run dev`, and a `procrastinate` worker (`cd backend && uv run procrastinate --app=app.jobs.app.app worker`), then upload a document and confirm it shows "Processing…" then flips to ready without you reloading the page.

---

## Milestone 2: Structure-aware chunking with semantic fallback

Everything here runs through the async pipeline from Milestone 1 automatically (it all executes inside `IngestionService.process()`).

### Task 7: `SemanticChunker` (local, `fastembed`-based)

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/rag/semantic_chunking.py`
- Test: `backend/tests/test_semantic_chunking.py`

**Interfaces:**
- Produces: `SemanticChunker(model_name: str = "BAAI/bge-small-en-v1.5", breakpoint_percentile: float = 85.0)` with `.split(text: str) -> list[str]`; `split_sentences(text: str) -> list[str]`.

- [ ] **Step 1: Add the dependency**

```toml
# backend/pyproject.toml — add to [project] dependencies:
    "fastembed>=0.4",
```

Run: `cd backend && uv sync`

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_semantic_chunking.py
from app.rag.semantic_chunking import SemanticChunker, split_sentences


def test_split_sentences_splits_on_sentence_boundaries():
    text = "First sentence. Second sentence! Third sentence?"
    assert split_sentences(text) == [
        "First sentence.", "Second sentence!", "Third sentence?",
    ]


def test_semantic_chunker_groups_related_sentences_and_splits_on_topic_shift():
    # Real fastembed model — this test needs network access on first run to
    # download the model (cached afterward). Two clearly unrelated topics should
    # end up in different chunks; assertions are deliberately loose on the exact
    # split point since real embedding similarity isn't perfectly deterministic
    # across model versions.
    chunker = SemanticChunker()
    text = (
        "Neurons are the basic building blocks of the brain. "
        "Each neuron connects to thousands of others via synapses. "
        "Photosynthesis converts sunlight into chemical energy in plants. "
        "Chlorophyll absorbs light primarily in the blue and red wavelengths."
    )
    chunks = chunker.split(text)
    assert len(chunks) >= 2
    assert any("neuron" in c.lower() for c in chunks)
    assert any("photosynthesis" in c.lower() for c in chunks)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_semantic_chunking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.semantic_chunking'`

- [ ] **Step 4: Implement**

```python
# backend/app/rag/semantic_chunking.py
import re

from fastembed import TextEmbedding

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticChunker:
    """Splits a structure-less block of text into sub-pieces at points where the
    meaning shifts, rather than at a fixed size. Used only as a FALLBACK — see
    Chunker._pieces_for_segment — when a segment is too large and has no finer
    heading/slide/paragraph structure to split on.

    Uses local sentence embeddings (fastembed, ONNX — no PyTorch, no network call
    per request) purely to find break points; the final chunk embeddings used for
    search are still produced by GeminiEmbeddingsProvider elsewhere in the pipeline.
    """

    def __init__(
        self, model_name: str = "BAAI/bge-small-en-v1.5", breakpoint_percentile: float = 85.0
    ) -> None:
        self._model = TextEmbedding(model_name=model_name)
        self._percentile = breakpoint_percentile

    def split(self, text: str) -> list[str]:
        sentences = split_sentences(text)
        if len(sentences) <= 1:
            return [text] if text.strip() else []

        vectors = list(self._model.embed(sentences))
        similarities = [_cosine(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
        # A breakpoint is a similarity LOW point. Convert to "distance" (1 - similarity)
        # and use a percentile threshold — the standard method for semantic chunking:
        # only the sharpest topic shifts (the top `100 - percentile`% of distances)
        # become chunk boundaries, everything else stays merged.
        distances = [1.0 - s for s in similarities]
        sorted_distances = sorted(distances)
        idx = min(int(len(sorted_distances) * self._percentile / 100), len(sorted_distances) - 1)
        threshold = sorted_distances[idx]

        pieces: list[str] = []
        current = [sentences[0]]
        for i, distance in enumerate(distances):
            if distance >= threshold:
                pieces.append(" ".join(current))
                current = [sentences[i + 1]]
            else:
                current.append(sentences[i + 1])
        pieces.append(" ".join(current))
        return pieces
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_semantic_chunking.py -v`
Expected: PASS (first run downloads the ONNX model — allow extra time)

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/rag/semantic_chunking.py backend/tests/test_semantic_chunking.py
git commit -m "feat(chunking): add local semantic chunking fallback via fastembed"
```

### Task 8: Rewrite `Chunker.split()` into the cascade

**Files:**
- Modify: `backend/app/rag/chunking.py`
- Test: `backend/tests/test_chunking.py`

**Interfaces:**
- Consumes: `SemanticChunker.split` (Task 7, injected as an optional constructor arg — `None` preserves old always-fixed-size behavior for any caller not wired with one).
- Produces: `Chunker(chunk_tokens, chunk_overlap_tokens, semantic_chunker: SemanticChunker | None = None)`. `Chunker.split` signature unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_chunking.py — add these cases (keep existing tests in the file):
from app.rag.chunking import Chunker
from app.rag.types import ParsedDocument, Segment


def test_segment_that_fits_stays_as_one_chunk_with_no_forced_split():
    chunker = Chunker(chunk_tokens=100, chunk_overlap_tokens=10)
    doc = ParsedDocument(segments=[Segment(text="A short segment.", section="Intro")])
    chunks = chunker.split(doc)
    assert len(chunks) == 1
    assert chunks[0].content == "A short segment."


def test_oversized_segment_without_semantic_chunker_uses_fixed_size_split():
    chunker = Chunker(chunk_tokens=5, chunk_overlap_tokens=1)
    long_text = " ".join(["word"] * 50)
    doc = ParsedDocument(segments=[Segment(text=long_text)])
    chunks = chunker.split(doc)
    assert len(chunks) > 1


def test_oversized_segment_with_semantic_chunker_delegates_to_it():
    class FakeSemanticChunker:
        def split(self, text: str) -> list[str]:
            return ["first half.", "second half."]

    chunker = Chunker(chunk_tokens=3, chunk_overlap_tokens=1, semantic_chunker=FakeSemanticChunker())
    long_text = " ".join(["word"] * 20)
    doc = ParsedDocument(segments=[Segment(text=long_text)])
    chunks = chunker.split(doc)
    assert [c.content for c in chunks] == ["first half.", "second half."]


def test_semantic_piece_still_too_large_falls_back_to_fixed_size_split():
    class FakeSemanticChunker:
        def split(self, text: str) -> list[str]:
            return [text]  # doesn't actually shrink it

    chunker = Chunker(chunk_tokens=3, chunk_overlap_tokens=1, semantic_chunker=FakeSemanticChunker())
    long_text = " ".join(["word"] * 20)
    doc = ParsedDocument(segments=[Segment(text=long_text)])
    chunks = chunker.split(doc)
    # Falls through to the fixed-size splitter as the final safety net.
    assert len(chunks) > 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: FAIL on the new tests (old always-splits behavior doesn't match the new "fits → keep as one" expectation).

- [ ] **Step 3: Implement the cascade**

```python
# backend/app/rag/chunking.py — full file replacement:
import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.semantic_chunking import SemanticChunker
from app.rag.types import Chunk, ParsedDocument

_ENCODING = "cl100k_base"


class Chunker:
    """Splits a ParsedDocument into chunks, WITHIN each segment (never merging
    across slides/pages/headings). For each segment:
      1. If it already fits in chunk_tokens, keep it as ONE chunk (no splitting).
      2. If it's too large and a SemanticChunker is configured, split it there
         first (meaning-based boundaries), then...
      3. ...anything still too large after that (or if no SemanticChunker is
         configured at all) falls back to fixed-size recursive splitting, same
         as this class's original behavior — the last-resort safety net.
    """

    def __init__(
        self,
        chunk_tokens: int,
        chunk_overlap_tokens: int,
        semantic_chunker: SemanticChunker | None = None,
    ) -> None:
        self._encoder = tiktoken.get_encoding(_ENCODING)
        self._chunk_tokens = chunk_tokens
        self._splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=_ENCODING,
            chunk_size=chunk_tokens,
            chunk_overlap=chunk_overlap_tokens,
        )
        self._semantic = semantic_chunker

    def split(self, document: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        index = 0
        for segment in document.segments:
            if not segment.text.strip():
                continue
            for piece in self._pieces_for_segment(segment.text):
                piece = piece.strip()
                if not piece:
                    continue
                chunks.append(
                    Chunk(
                        content=piece,
                        chunk_index=index,
                        page_number=segment.page_number,
                        section=segment.section,
                        token_count=len(self._encoder.encode(piece)),
                    )
                )
                index += 1
        return chunks

    def _pieces_for_segment(self, text: str) -> list[str]:
        if len(self._encoder.encode(text)) <= self._chunk_tokens:
            return [text]

        if self._semantic is None:
            return self._splitter.split_text(text)

        pieces: list[str] = []
        for piece in self._semantic.split(text):
            if len(self._encoder.encode(piece)) <= self._chunk_tokens:
                pieces.append(piece)
            else:
                pieces.extend(self._splitter.split_text(piece))
        return pieces
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: PASS (all tests, including the pre-existing ones — check they weren't asserting the old "always splits" behavior; if any pre-existing test in this file relies on a segment shorter than `chunk_tokens` still getting split, that test's assertion was testing the old, now-deliberately-changed behavior and should be updated to match the new "fits → one chunk" rule, not treated as a regression).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/chunking.py backend/tests/test_chunking.py
git commit -m "feat(chunking): structure-first cascade with semantic and fixed-size fallbacks"
```

### Task 9: Shared heading-split helper + `TextParser` breadcrumb upgrade

Factors the existing flush-on-heading pattern (currently duplicated conceptually between `TextParser._split_markdown` and `DocxParser`) into one shared, multi-level-aware helper, and upgrades it from a single flat heading to a breadcrumb stack (e.g. `"Lecture 4 > Neural Networks"`).

**Files:**
- Create: `backend/app/rag/parsing/heading_split.py`
- Modify: `backend/app/rag/parsing/text.py`
- Test: `backend/tests/test_parsing.py`

**Interfaces:**
- Produces: `split_markdown_by_headings(text: str, page_number: int | None = None) -> list[Segment]` (used here by `TextParser`, and again by the PDF parser in Task 11).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_parsing.py — add:
from app.rag.parsing.text import TextParser


def test_markdown_nested_headings_produce_breadcrumb_sections():
    md = "# Lecture 4\nIntro text.\n## Neural Networks\nBody about neurons.\n"
    parsed = TextParser().parse(md.encode(), "text/markdown")
    sections = [s.section for s in parsed.segments]
    assert "Lecture 4" in sections
    assert "Lecture 4 > Neural Networks" in sections
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_parsing.py::test_markdown_nested_headings_produce_breadcrumb_sections -v`
Expected: FAIL — current `_split_markdown` only tracks one flat heading, so nested breadcrumb text isn't produced.

- [ ] **Step 3: Implement the shared helper**

```python
# backend/app/rag/parsing/heading_split.py
from app.rag.types import Segment


def split_markdown_by_headings(text: str, page_number: int | None = None) -> list[Segment]:
    """Splits Markdown text on '#' heading lines into one Segment per heading
    block. `section` is a '>'-joined breadcrumb of the current heading stack
    (e.g. 'Lecture 4 > Neural Networks'), not just the innermost heading, so
    nested structure survives into citations. Shared by TextParser's .md path
    and the PDF parser (over pymupdf4llm's Markdown output)."""
    segments: list[Segment] = []
    heading_stack: list[tuple[int, str]] = []  # (level, text), outermost first
    buffer: list[str] = []

    def breadcrumb() -> str | None:
        return " > ".join(h[1] for h in heading_stack) or None

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or heading_stack:
            segments.append(Segment(text=body, section=breadcrumb(), page_number=page_number))

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            flush()
            buffer = []
            level = len(stripped) - len(stripped.lstrip("#"))
            heading_text = stripped.lstrip("#").strip()
            # Pop headings at this level or deeper — keeps the stack representing
            # the current nesting path (e.g. a new "##" replaces the previous "##"
            # but keeps any enclosing "#").
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_text))
        else:
            buffer.append(line)
    flush()
    return segments or [Segment(text=text, page_number=page_number)]
```

- [ ] **Step 4: Point `TextParser` at the shared helper**

```python
# backend/app/rag/parsing/text.py — full file replacement:
from app.rag.parsing.heading_split import split_markdown_by_headings
from app.rag.types import ParsedDocument, Segment


class TextParser:
    """TXT -> one segment. Markdown -> one segment per heading block, with a
    breadcrumb `section` for nested headings (see heading_split.py)."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        text = data.decode("utf-8", errors="replace")
        if content_type != "text/markdown":
            return ParsedDocument(segments=[Segment(text=text)], page_count=None)
        return ParsedDocument(segments=split_markdown_by_headings(text), page_count=None)
```

- [ ] **Step 5: Run test to verify it passes, then the full parsing test file**

Run: `cd backend && uv run pytest tests/test_parsing.py -v`
Expected: PASS (all tests — check any pre-existing markdown test asserting the OLD flat-heading `section` value; update its expected value to match the new breadcrumb format if so, since that's the intended behavior change, not a regression).

- [ ] **Step 6: Commit**

```bash
git add backend/app/rag/parsing/heading_split.py backend/app/rag/parsing/text.py backend/tests/test_parsing.py
git commit -m "feat(parsing): shared multi-level heading-breadcrumb splitter for Markdown"
```

### Task 10: `DocxParser` breadcrumb upgrade

**Files:**
- Modify: `backend/app/rag/parsing/office.py`
- Test: `backend/tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_parsing.py — add (uses python-docx directly to build a fixture,
# matching however this file's existing DOCX tests already construct one — check
# for an existing `_build_docx`-style helper in this file and reuse it if present):
import io

from docx import Document as DocxDocument

from app.rag.parsing.office import DocxParser


def test_docx_nested_headings_produce_breadcrumb_sections():
    doc = DocxDocument()
    doc.add_heading("Lecture 4", level=1)
    doc.add_paragraph("Intro text.")
    doc.add_heading("Neural Networks", level=2)
    doc.add_paragraph("Body about neurons.")
    buf = io.BytesIO()
    doc.save(buf)

    parsed = DocxParser().parse(buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    sections = [s.section for s in parsed.segments]
    assert "Lecture 4" in sections
    assert "Lecture 4 > Neural Networks" in sections
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_parsing.py::test_docx_nested_headings_produce_breadcrumb_sections -v`
Expected: FAIL — current `DocxParser` tracks only one flat `current_heading`.

- [ ] **Step 3: Implement the breadcrumb stack for `DocxParser`**

```python
# backend/app/rag/parsing/office.py — replace only the DocxParser class (PptxParser unchanged):
import re

_HEADING_STYLE_RE = re.compile(r"heading\s*(\d+)", re.IGNORECASE)


class DocxParser:
    """Segments split on heading-styled paragraphs, with a '>'-joined breadcrumb
    `section` for nested heading levels (mirrors heading_split.py's approach, but
    the level signal here is the paragraph's Word STYLE, e.g. 'Heading 2', not a
    '#' count)."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        doc = DocxDocument(io.BytesIO(data))
        segments: list[Segment] = []
        heading_stack: list[tuple[int, str]] = []
        buffer: list[str] = []

        def breadcrumb() -> str | None:
            return " > ".join(h[1] for h in heading_stack) or None

        def flush() -> None:
            body = "\n".join(buffer).strip()
            if body or heading_stack:
                segments.append(Segment(text=body, section=breadcrumb()))

        for para in doc.paragraphs:
            style = (para.style.name or "") if para.style else ""
            match = _HEADING_STYLE_RE.match(style.strip())
            if match and para.text.strip():
                flush()
                buffer = []
                level = int(match.group(1))
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, para.text.strip()))
            elif para.text.strip():
                buffer.append(para.text)
        flush()
        if not segments:
            segments = [Segment(text="")]
        return ParsedDocument(segments=segments, page_count=None)
```

- [ ] **Step 4: Run test to verify it passes, then the full parsing test file**

Run: `cd backend && uv run pytest tests/test_parsing.py -v`
Expected: PASS (update any pre-existing DOCX heading test's expected flat-heading value to the new breadcrumb format, same reasoning as Task 9 Step 5).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/parsing/office.py backend/tests/test_parsing.py
git commit -m "feat(parsing): heading-breadcrumb sections for DOCX"
```

### Task 11: PDF parser rewrite via `pymupdf4llm`

The real structural gap: PDF currently segments per-page with no heading awareness at all.

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/rag/parsing/pdf.py`
- Test: `backend/tests/test_parsing.py`

**Interfaces:**
- Consumes: `split_markdown_by_headings` (Task 9), existing `OcrProvider` port (unchanged).
- Produces: `PdfParser` keeps the exact same public shape (`__init__(ocr, ocr_enabled, min_chars)`, `.parse(data, content_type) -> ParsedDocument`) — only its internals change.

- [ ] **Step 1: Add the dependency**

```toml
# backend/pyproject.toml — add to [project] dependencies:
    "pymupdf4llm>=0.0.17",
```

Run: `cd backend && uv sync`

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_parsing.py — add (build a real small PDF with fpdf2, already a
# dev dependency per pyproject.toml, matching however existing PDF tests in this
# file construct their fixture — reuse that helper if one already exists):
from app.rag.parsing.pdf import PdfParser
from tests.fakes import FakeOcrProvider


def test_pdf_headings_produce_breadcrumb_sections(tmp_path):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Lecture 4", ln=True)
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 10, "Intro text about the lecture.", ln=True)
    data = pdf.output()

    parser = PdfParser(ocr=FakeOcrProvider(), ocr_enabled=True, min_chars=5)
    parsed = parser.parse(bytes(data), "application/pdf")

    assert parsed.page_count == 1
    assert any(s.section for s in parsed.segments)  # at least one heading detected
```

Note: exact heading detection depends on `pymupdf4llm`'s font-size heuristic actually recognizing "Lecture 4" as a heading in this minimal fixture — if it doesn't fire reliably on a bare `fpdf2`-generated PDF (real lecture PDFs have much clearer visual hierarchy than a minimal test fixture), relax the assertion to checking `parsed.page_count == 1` and that segments are non-empty, and instead verify heading-breadcrumb behavior via a unit test directly against `split_markdown_by_headings` (already covered in Task 9) fed a literal Markdown string containing what `pymupdf4llm` is documented to emit for a detected heading (a `#`-prefixed line) — this decouples "does our breadcrumb logic work" (deterministic, already tested) from "does pymupdf4llm's font-size heuristic fire on this exact fixture" (heuristic, less reliable to pin down in a unit test).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_parsing.py::test_pdf_headings_produce_breadcrumb_sections -v`
Expected: FAIL — `PdfParser` still uses `pypdf`, page-only segmentation, no headings.

- [ ] **Step 4: Implement**

```python
# backend/app/rag/parsing/pdf.py
import io

import fitz  # PyMuPDF — used directly only to check per-page text length for the
             # OCR-fallback decision; pymupdf4llm does the actual extraction below.
import pymupdf4llm
from pdf2image import convert_from_bytes

from app.rag.ocr import OcrProvider
from app.rag.parsing.heading_split import split_markdown_by_headings
from app.rag.types import ParsedDocument, Segment


class PdfParser:
    """Extracts Markdown (with inferred '#' headings from font-size/style signals)
    via pymupdf4llm, then splits on those headings the same way TextParser does —
    replacing the old page-only, heading-blind pypdf-based segmentation. Any page
    pymupdf4llm can't extract enough text from (a scanned page) is OCR'd separately
    (pdf2image, requires poppler) and appended as its own heading-less segment,
    same fallback role the old parser's per-page OCR played.

    VERIFY DURING IMPLEMENTATION: this uses pymupdf4llm.to_markdown(doc)'s
    documented default behavior (whole-document Markdown, headings inferred from
    font size). If the installed version's signature differs, adjust the call
    accordingly — check `pymupdf4llm.to_markdown.__doc__` for the installed version.
    """

    def __init__(self, ocr: OcrProvider, ocr_enabled: bool, min_chars: int) -> None:
        self._ocr = ocr
        self._ocr_enabled = ocr_enabled
        self._min_chars = min_chars

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        doc = fitz.open(stream=data, filetype="pdf")
        page_count = doc.page_count

        # Pages pymupdf4llm likely can't get usable text from (near-empty embedded
        # text layer — same heuristic the old parser used) get OCR'd separately.
        scanned_pages = [
            i for i in range(page_count) if len((doc[i].get_text() or "").strip()) < self._min_chars
        ]

        md_text = pymupdf4llm.to_markdown(doc)
        segments = split_markdown_by_headings(md_text)

        if self._ocr_enabled:
            for page_index in scanned_pages:
                image = _render_page(data, page_index)
                ocr_text = self._ocr.extract_text(image).strip()
                if ocr_text:
                    segments.append(Segment(text=ocr_text, page_number=page_index + 1))

        return ParsedDocument(segments=segments, page_count=page_count)


def _render_page(data: bytes, page_index: int):
    images = convert_from_bytes(data, first_page=page_index + 1, last_page=page_index + 1)
    return images[0]
```

- [ ] **Step 5: Run test to verify it passes, then the full parsing test file**

Run: `cd backend && uv run pytest tests/test_parsing.py -v`
Expected: PASS. If the heading-detection assertion is flaky against the minimal `fpdf2` fixture per the Step 2 note, relax it as described there rather than fighting the heuristic on an artificial fixture — real-world PDFs (actual lecture slides/notes) have much clearer visual hierarchy for `pymupdf4llm` to key off.

- [ ] **Step 6: Manual smoke test against a real PDF**

Run the parser against an actual multi-page lecture PDF (not just the test fixture) via a throwaway script or a REPL, and eyeball that `page_count` and `segments` look sane (headings detected where the PDF visually has them, OCR fallback still firing on any scanned pages) — this is the step that actually validates `pymupdf4llm`'s heuristic quality on real content, since the unit test fixture is deliberately minimal.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/rag/parsing/pdf.py backend/tests/test_parsing.py
git commit -m "feat(parsing): PDF heading-aware chunking via pymupdf4llm"
```

### Task 12: `DocumentChunk.content_hash` (needed by Milestone 3's diffing)

**Files:**
- Modify: `backend/app/models/document.py`
- Create: `backend/app/db/migrations/versions/0006_chunk_content_hash.py`
- Modify: `backend/app/services/ingestion.py`
- Test: `backend/tests/test_ingestion_service.py`

**Interfaces:**
- Produces: `DocumentChunk.content_hash: str` (SHA-256 of `content`, computed in `IngestionService.process`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ingestion_service.py — add:
import hashlib


@pytest.mark.asyncio
async def test_process_stores_content_hash_per_chunk(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"first paragraph of notes. second paragraph of notes.",
    )

    await svc.process(staged.id)

    chunks = await ChunkRepository(db_session).list()
    assert all(c.content_hash == hashlib.sha256(c.content.encode()).hexdigest() for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ingestion_service.py::test_process_stores_content_hash_per_chunk -v`
Expected: FAIL — `AttributeError` (no `content_hash` column yet).

- [ ] **Step 3: Add the column**

```python
# backend/app/models/document.py — add to DocumentChunk, after section:
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
```

- [ ] **Step 4: Write the migration**

```python
# backend/app/db/migrations/versions/0006_chunk_content_hash.py
"""add content_hash to document_chunks

Revision ID: 0006_chunk_content_hash
Revises: 0005_document_status
Create Date: 2026-07-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_chunk_content_hash"
down_revision = "0005_document_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks", sa.Column("content_hash", sa.String(length=64), nullable=False, server_default="")
    )
    op.create_index("ix_document_chunks_content_hash", "document_chunks", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_column("document_chunks", "content_hash")
```

- [ ] **Step 5: Compute and store it in `process()`**

```python
# backend/app/services/ingestion.py — inside process(), the add_many dict gains one field:
                await self._chunks.add_many(
                    [
                        dict(
                            document_id=document.id,
                            user_id=document.user_id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            content_hash=hashlib.sha256(chunk.content.encode()).hexdigest(),
                            token_count=chunk.token_count,
                            page_number=chunk.page_number,
                            section=chunk.section,
                            embedding=vector,
                        )
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ]
                )
```

- [ ] **Step 6: Apply the migration and run tests**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/test_ingestion_service.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/document.py backend/app/db/migrations/versions/0006_chunk_content_hash.py backend/app/services/ingestion.py backend/tests/test_ingestion_service.py
git commit -m "feat(ingestion): store per-chunk content_hash for future replace-diffing"
```

### Task 13: Wire `SemanticChunker` into DI

**Files:**
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/jobs/ingestion_tasks.py`

**Interfaces:**
- Consumes: `SemanticChunker` (Task 7).
- Produces: `get_chunker()` and the background task's `Chunker(...)` both now pass a real `SemanticChunker`.

- [ ] **Step 1: Update `get_chunker` in `deps.py`**

```python
# backend/app/api/deps.py — replace get_chunker:
from app.rag.semantic_chunking import SemanticChunker


def get_chunker(settings: Settings = Depends(get_settings)) -> Chunker:  # noqa: B008
    return Chunker(
        chunk_tokens=settings.chunk_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        semantic_chunker=SemanticChunker(),
    )
```

- [ ] **Step 2: Update the background task's `Chunker` construction**

```python
# backend/app/jobs/ingestion_tasks.py — update the Chunker(...) call inside process_document:
from app.rag.semantic_chunking import SemanticChunker

# ... inside process_document, replace the Chunker(...) construction:
            chunker=Chunker(
                chunk_tokens=settings.chunk_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
                semantic_chunker=SemanticChunker(),
            ),
```

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS (no test constructs `IngestionService`/`Chunker` through `get_chunker` directly in a way that would download the model unexpectedly — tests use the explicit `Chunker(chunk_tokens=..., chunk_overlap_tokens=...)` construction with no `semantic_chunker` arg, which stays `None` and skips the fastembed path entirely).

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/deps.py backend/app/jobs/ingestion_tasks.py
git commit -m "feat(chunking): wire SemanticChunker into the real ingestion path"
```

**Milestone 2 checkpoint:** Upload a real lecture PDF/PPTX/DOCX and confirm (via `GET /documents/{id}` chunk inspection, or a quick DB query) that `section` values reflect real heading structure (with breadcrumbs for nested headings), and that a short segment is no longer force-split into multiple tiny chunks.

---

## Milestone 3: Dedup & versioning (Replace flow)

### Task 14: `ChunkRepository` diffing primitives

**Files:**
- Modify: `backend/app/db/repositories/chunk.py`
- Test: `backend/tests/test_chunk_repository.py`

**Interfaces:**
- Produces: `ChunkRepository.get_hashes_for_document(document_id) -> dict[str, uuid.UUID]` (content_hash → chunk id), `ChunkRepository.update_chunk_position(chunk_id, *, chunk_index, page_number, section) -> None`, `ChunkRepository.delete_by_ids(chunk_ids: list[uuid.UUID]) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_chunk_repository.py — add. Self-contained helpers below build a
# user + document + chunks directly (check whether this file already has an
# equivalent `_user`/`_document` helper before adding a second one — if so, reuse
# it and drop the local copies here instead):
import uuid

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.models.document import DocumentChunk


async def _user(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


async def _document(db_session, user_id):
    return await DocumentRepository(db_session).create(
        user_id=user_id,
        filename="notes.txt",
        content_type="text/plain",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/notes.txt",
        file_size=1,
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )


def _chunk_row(document_id, user_id, chunk_index, content_hash):
    return dict(
        document_id=document_id,
        user_id=user_id,
        chunk_index=chunk_index,
        content=f"content for {content_hash}",
        content_hash=content_hash,
        token_count=3,
        page_number=None,
        section=None,
        embedding=[0.0] * 1536,
    )


@pytest.mark.asyncio
async def test_get_hashes_for_document_maps_hash_to_chunk_id(db_session):
    user = await _user(db_session)
    document = await _document(db_session, user.id)
    await ChunkRepository(db_session).add_many(
        [_chunk_row(document.id, user.id, 0, "h1"), _chunk_row(document.id, user.id, 1, "h2")]
    )

    hashes = await ChunkRepository(db_session).get_hashes_for_document(document.id)
    assert set(hashes.keys()) == {"h1", "h2"}


@pytest.mark.asyncio
async def test_delete_by_ids_removes_only_given_chunks(db_session):
    user = await _user(db_session)
    document = await _document(db_session, user.id)
    await ChunkRepository(db_session).add_many(
        [
            _chunk_row(document.id, user.id, 0, "h1"),
            _chunk_row(document.id, user.id, 1, "h2"),
            _chunk_row(document.id, user.id, 2, "h3"),
        ]
    )
    chunks = {c.content_hash: c.id for c in await ChunkRepository(db_session).list()}

    await ChunkRepository(db_session).delete_by_ids([chunks["h1"], chunks["h2"]])

    remaining = await ChunkRepository(db_session).list()
    assert [c.id for c in remaining] == [chunks["h3"]]


@pytest.mark.asyncio
async def test_update_chunk_position_updates_index_page_and_section(db_session):
    user = await _user(db_session)
    document = await _document(db_session, user.id)
    await ChunkRepository(db_session).add_many([_chunk_row(document.id, user.id, 0, "h1")])
    chunk = (await ChunkRepository(db_session).list())[0]

    await ChunkRepository(db_session).update_chunk_position(
        chunk.id, chunk_index=5, page_number=9, section="New Section"
    )

    updated = await db_session.get(DocumentChunk, chunk.id)
    assert (updated.chunk_index, updated.page_number, updated.section) == (5, 9, "New Section")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_chunk_repository.py -v`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Implement**

```python
# backend/app/db/repositories/chunk.py — add to ChunkRepository, add `delete` to the
# sqlalchemy import at the top of the file (`from sqlalchemy import delete, select`):

    async def get_hashes_for_document(self, document_id: uuid.UUID) -> dict[str, uuid.UUID]:
        """Maps each existing chunk's content_hash -> its row id, for one document —
        used by Replace to decide which chunks can be left alone vs deleted."""
        stmt = select(DocumentChunk.content_hash, DocumentChunk.id).where(
            DocumentChunk.document_id == document_id
        )
        result = await self._session.execute(stmt)
        return {row.content_hash: row.id for row in result.all()}

    async def update_chunk_position(
        self,
        chunk_id: uuid.UUID,
        *,
        chunk_index: int,
        page_number: int | None,
        section: str | None,
    ) -> None:
        """Repositions a RETAINED chunk (its content_hash matched an old chunk, so
        its embedding is still valid) to reflect where it sits in the newly
        reprocessed document."""
        chunk = await self.get(chunk_id)
        if chunk is None:
            return
        chunk.chunk_index = chunk_index
        chunk.page_number = page_number
        chunk.section = section
        await self._session.flush()

    async def delete_by_ids(self, chunk_ids: list[uuid.UUID]) -> None:
        if not chunk_ids:
            return
        stmt = delete(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
        await self._session.execute(stmt)
        await self._session.flush()
```

- [ ] **Step 4: Run tests to verify they pass, then the full chunk repository test file**

Run: `cd backend && uv run pytest tests/test_chunk_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/repositories/chunk.py backend/tests/test_chunk_repository.py
git commit -m "feat(replace): chunk-diffing repository primitives"
```

### Task 15: `IngestionService.stage_replace()` + `process_replace()`

The core diffing logic. Deliberately keeps the OLD document fully "ready" and searchable throughout — the new file is parsed/chunked/diffed against the old chunk-hash set BEFORE anything about the existing document row is touched, so a failed Replace leaves the previously-working document completely intact (just tagged `failed` until retried).

**Files:**
- Modify: `backend/app/services/ingestion.py`
- Test: `backend/tests/test_ingestion_service.py`

**Interfaces:**
- Consumes: `ChunkRepository.get_hashes_for_document`/`update_chunk_position`/`delete_by_ids` (Task 14).
- Produces: `IngestionService.stage_replace(document_id, data) -> tuple[Document, bool]` (bool = `no_changes_detected`), `IngestionService.process_replace(document_id: uuid.UUID, new_storage_path: str, new_content_hash: str, new_file_size: int) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ingestion_service.py — add:
@pytest.mark.asyncio
async def test_stage_replace_short_circuits_on_identical_content(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    data = b"first paragraph of notes. second paragraph of notes."
    staged = await svc.stage(user_id=user.id, filename="notes.txt", content_type="text/plain", data=data)
    await svc.process(staged.id)

    document, no_changes = await svc.stage_replace(staged.id, data)

    assert no_changes is True
    assert document.status == "ready"  # untouched


@pytest.mark.asyncio
async def test_process_replace_reuses_unchanged_chunks_and_embeds_only_new_ones(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    original = b"Section A content. Section B content."
    staged = await svc.stage(user_id=user.id, filename="notes.txt", content_type="text/plain", data=original)
    await svc.process(staged.id)
    original_chunks = {c.content: c.id for c in await ChunkRepository(db_session).list()}

    updated = b"Section A content. Section B CHANGED content."
    document, no_changes = await svc.stage_replace(staged.id, updated)
    assert no_changes is False
    await svc.process_replace(
        document.id, document.storage_path, document.content_hash, document.file_size
    )

    final_chunks = await ChunkRepository(db_session).list()
    final_contents = {c.content for c in final_chunks}
    assert "Section A content." in final_contents  # unchanged chunk kept
    assert "Section A content." in original_chunks  # and it's literally the same row
    unchanged_id = next(c.id for c in final_chunks if c.content == "Section A content.")
    assert unchanged_id == original_chunks["Section A content."]  # same row, not re-inserted


@pytest.mark.asyncio
async def test_process_replace_failure_leaves_old_document_intact(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    svc = _service(db_session, storage)
    staged = await svc.stage(user_id=user.id, filename="notes.txt", content_type="text/plain", data=b"original text here")
    await svc.process(staged.id)
    before_hash = staged.content_hash
    before_chunk_count = (await DocumentRepository(db_session).get(staged.id)).chunk_count

    boom_svc = _service(db_session, storage, embeddings=BoomEmbeddings())
    document, no_changes = await boom_svc.stage_replace(staged.id, b"totally different new text")
    assert no_changes is False

    with pytest.raises(RuntimeError):
        await boom_svc.process_replace(
            document.id, document.storage_path, document.content_hash, document.file_size
        )

    reloaded = await DocumentRepository(db_session).get(staged.id)
    assert reloaded.status == "failed"
    assert reloaded.content_hash == before_hash  # old version's identity preserved
    assert reloaded.chunk_count == before_chunk_count  # old chunks untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_ingestion_service.py -v`
Expected: FAIL — `stage_replace`/`process_replace` don't exist yet.

- [ ] **Step 3: Implement**

```python
# backend/app/services/ingestion.py — add to IngestionService:

    async def stage_replace(self, document_id: uuid.UUID, data: bytes) -> tuple[Document, bool]:
        """Fast half of Replace: hash the new file first. If it matches the
        document's CURRENT hash, short-circuit — no work at all. Otherwise, save
        the new file bytes under a NEW storage path (the old file/chunks are left
        completely alone until process_replace succeeds) and mark 'processing'."""
        document = await self._documents.get(document_id)
        if document is None:
            raise IngestionError(f"Document {document_id} not found")

        new_hash = hashlib.sha256(data).hexdigest()
        if new_hash == document.content_hash:
            return document, True

        new_storage_path = self._storage.save(document.user_id, document.filename, data)
        await self._documents.set_status(document.id, "processing")
        await self._session.commit()
        # Stamp the new identity now so the caller/enqueue step has what it needs
        # to pass to process_replace — but note the DB row's content_hash/chunk_count
        # are NOT updated here; that only happens once process_replace succeeds.
        document.storage_path = new_storage_path
        document.content_hash = new_hash
        document.file_size = len(data)
        return document, False

    async def process_replace(
        self,
        document_id: uuid.UUID,
        new_storage_path: str,
        new_content_hash: str,
        new_file_size: int,
    ) -> None:
        """Heavy half of Replace: parse+chunk the new file, diff its chunk hashes
        against the document's EXISTING chunks (fetched via get_hashes_for_document,
        keyed on the OLD content, since the DB row's own content_hash/storage_path
        haven't been overwritten yet), reuse what's unchanged, embed only what's
        new, delete what's gone, then atomically flip the document over to the
        new version. On failure, the document is restored to 'ready' with its
        OLD identity untouched — it never actually lost anything."""
        document = await self._documents.get(document_id)
        if document is None:
            return

        old_hash_to_id = await self._chunks.get_hashes_for_document(document.id)
        old_storage_path = document.storage_path  # the path BEFORE this replace (still on disk)

        try:
            data = self._storage.read(new_storage_path)
            parsed = self._parser.parse(data, document.content_type)
            new_chunks = self._chunker.split(parsed)
            new_hashes = [hashlib.sha256(c.content.encode()).hexdigest() for c in new_chunks]

            to_embed_indices = [i for i, h in enumerate(new_hashes) if h not in old_hash_to_id]
            vectors = self._embeddings.embed_documents(
                [new_chunks[i].content for i in to_embed_indices]
            )
            vector_by_index = dict(zip(to_embed_indices, vectors, strict=True))

            async with self._session.begin_nested():
                new_rows = []
                for i, (chunk, content_hash) in enumerate(zip(new_chunks, new_hashes, strict=True)):
                    if content_hash in old_hash_to_id:
                        # Unchanged content — keep the existing row, just reposition it.
                        await self._chunks.update_chunk_position(
                            old_hash_to_id[content_hash],
                            chunk_index=chunk.chunk_index,
                            page_number=chunk.page_number,
                            section=chunk.section,
                        )
                    else:
                        new_rows.append(
                            dict(
                                document_id=document.id,
                                user_id=document.user_id,
                                chunk_index=chunk.chunk_index,
                                content=chunk.content,
                                content_hash=content_hash,
                                token_count=chunk.token_count,
                                page_number=chunk.page_number,
                                section=chunk.section,
                                embedding=vector_by_index[i],
                            )
                        )
                if new_rows:
                    await self._chunks.add_many(new_rows)

                # Any old chunk whose hash isn't in the new set is gone from the document.
                stale_ids = [
                    chunk_id
                    for content_hash, chunk_id in old_hash_to_id.items()
                    if content_hash not in set(new_hashes)
                ]
                await self._chunks.delete_by_ids(stale_ids)

                document.storage_path = new_storage_path
                document.content_hash = new_content_hash
                document.file_size = new_file_size
                document.page_count = parsed.page_count
                document.chunk_count = len(new_chunks)
                document.status = "ready"
            await self._session.commit()
            self._storage.delete(old_storage_path)  # superseded — safe to remove now
        except Exception as exc:
            await self._session.rollback()
            # Restore the OLD identity — this replace attempt never happened, as
            # far as the document's searchable content is concerned.
            document = await self._documents.get(document_id)
            if document is not None:
                document.status = "failed"
                document.error_message = str(exc)
            self._storage.delete(new_storage_path)  # the attempted new file, never adopted
            await self._session.commit()
            raise
```

Note the `Document` model needs `content_hash`/`storage_path`/`file_size`/`page_count`/`chunk_count`/`status`/`error_message` to already be plain mutable attributes on the ORM instance (they are — no schema change needed here beyond what Tasks 1/12 already added).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_ingestion_service.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ingestion.py backend/tests/test_ingestion_service.py
git commit -m "feat(replace): diff-based chunk reuse for document replacement"
```

### Task 16: Background job + enqueue seam for Replace

**Files:**
- Modify: `backend/app/jobs/ingestion_tasks.py`
- Modify: `backend/app/api/deps.py`
- Test: `backend/tests/test_ingestion_tasks.py`

**Interfaces:**
- Consumes: `IngestionService.process_replace` (Task 15).
- Produces: `process_document_replace` task (`name="process_document_replace"`), `enqueue_document_replace(document_id, new_storage_path, new_content_hash, new_file_size) -> None`, `get_enqueue_replace()` dependency.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ingestion_tasks.py — add:
@pytest.mark.asyncio
async def test_process_document_replace_task_delegates_to_ingestion_service():
    from app.jobs import ingestion_tasks

    document_id = uuid.uuid4()
    fake_process_replace = AsyncMock()

    with patch("app.jobs.ingestion_tasks.IngestionService") as FakeService:
        FakeService.return_value.process_replace = fake_process_replace
        await ingestion_tasks.process_document_replace(
            document_id=str(document_id),
            new_storage_path="/tmp/new.txt",
            new_content_hash="abc123",
            new_file_size=42,
        )

    fake_process_replace.assert_awaited_once_with(document_id, "/tmp/new.txt", "abc123", 42)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ingestion_tasks.py::test_process_document_replace_task_delegates_to_ingestion_service -v`
Expected: FAIL — task doesn't exist.

- [ ] **Step 3: Add the task**

```python
# backend/app/jobs/ingestion_tasks.py — add alongside process_document (reuses the
# same IngestionService-construction shape):
@app.task(name="process_document_replace")
async def process_document_replace(
    document_id: str, new_storage_path: str, new_content_hash: str, new_file_size: int
) -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        ocr = TesseractOcr(language=settings.ocr_language, cmd=settings.tesseract_cmd)
        service = IngestionService(
            session=session,
            documents=DocumentRepository(session),
            chunks=ChunkRepository(session),
            storage=LocalFileStorage(settings.upload_dir),
            parser=ParserDispatcher(
                ocr=ocr,
                ocr_enabled=settings.ocr_enabled,
                min_chars=settings.pdf_ocr_min_chars_per_page,
            ),
            chunker=Chunker(
                chunk_tokens=settings.chunk_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
                semantic_chunker=SemanticChunker(),
            ),
            embeddings=GeminiEmbeddingsProvider(settings),
            embedding_model=settings.embedding_model,
            embedding_dimension=settings.embedding_dimension,
        )
        await service.process_replace(
            uuid.UUID(document_id), new_storage_path, new_content_hash, new_file_size
        )
```

- [ ] **Step 4: Add the enqueue seam**

```python
# backend/app/api/deps.py — add:
from app.jobs.ingestion_tasks import process_document, process_document_replace


async def enqueue_document_replace(
    document_id: uuid.UUID, new_storage_path: str, new_content_hash: str, new_file_size: int
) -> None:
    await process_document_replace.defer_async(
        document_id=str(document_id),
        new_storage_path=new_storage_path,
        new_content_hash=new_content_hash,
        new_file_size=new_file_size,
    )


def get_enqueue_replace() -> Callable[[uuid.UUID, str, str, int], Awaitable[None]]:
    return enqueue_document_replace
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_ingestion_tasks.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/jobs/ingestion_tasks.py backend/app/api/deps.py backend/tests/test_ingestion_tasks.py
git commit -m "feat(replace): background job for document replacement"
```

### Task 17: `POST /documents/{document_id}/replace` endpoint

**Files:**
- Modify: `backend/app/api/documents.py`
- Modify: `backend/app/schemas/document.py`
- Test: `backend/tests/test_documents_api.py`

**Interfaces:**
- Consumes: `IngestionService.stage_replace` (Task 15), `get_enqueue_replace` (Task 16).
- Produces: `ReplaceDocumentResponse` schema (`document: DocumentResponse`, `no_changes: bool`).

- [ ] **Step 1: Add the response schema**

```python
# backend/app/schemas/document.py — add:
class ReplaceDocumentResponse(BaseModel):
    document: DocumentResponse
    no_changes: bool
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_documents_api.py — add:
@pytest.mark.asyncio
async def test_replace_with_identical_content_short_circuits(client, auth_headers, monkeypatch):
    upload = await client.post(
        "/documents",
        files={"file": ("notes.txt", b"same content", "text/plain")},
        headers=auth_headers,
    )
    document_id = upload.json()["id"]

    r = await client.post(
        f"/documents/{document_id}/replace",
        files={"file": ("notes.txt", b"same content", "text/plain")},
        headers=auth_headers,
    )

    assert r.status_code == 200
    assert r.json()["no_changes"] is True


@pytest.mark.asyncio
async def test_replace_with_new_content_enqueues_processing(client, auth_headers):
    from app.api import deps

    enqueued: list[tuple] = []

    async def fake_enqueue(document_id, new_storage_path, new_content_hash, new_file_size):
        enqueued.append((str(document_id), new_content_hash))

    app_.dependency_overrides[deps.get_enqueue_replace] = lambda: fake_enqueue

    upload = await client.post(
        "/documents",
        files={"file": ("notes.txt", b"original content", "text/plain")},
        headers=auth_headers,
    )
    document_id = upload.json()["id"]

    r = await client.post(
        f"/documents/{document_id}/replace",
        files={"file": ("notes.txt", b"changed content", "text/plain")},
        headers=auth_headers,
    )

    assert r.status_code == 200
    assert r.json()["no_changes"] is False
    assert len(enqueued) == 1
    assert enqueued[0][0] == document_id

    app_.dependency_overrides.pop(deps.get_enqueue_replace, None)
```

(Match the exact `app_`/`client`/`auth_headers` names already established in this file, as in Task 5.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_documents_api.py -v -k replace`
Expected: FAIL — 404, endpoint doesn't exist.

- [ ] **Step 4: Implement the endpoint**

```python
# backend/app/api/documents.py — add:
from app.api.deps import get_enqueue_replace
from app.schemas.document import ReplaceDocumentResponse


@router.post("/{document_id}/replace", response_model=ReplaceDocumentResponse)
async def replace_document(
    document_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
    service: IngestionService = Depends(get_ingestion_service),  # noqa: B008
    enqueue: Callable[[uuid.UUID, str, str, int], Awaitable[None]] = Depends(get_enqueue_replace),  # noqa: B008
) -> ReplaceDocumentResponse:
    # Ownership check up front — a missing OR not-yours id both 404, same pattern
    # as delete_document below.
    existing = await DocumentRepository(session).get_for_user(document_id, current_user.id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    document, no_changes = await service.stage_replace(document_id, data)
    if not no_changes:
        await enqueue(document.id, document.storage_path, document.content_hash, document.file_size)
    return ReplaceDocumentResponse(document=DocumentResponse.model_validate(document), no_changes=no_changes)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_documents_api.py -v`
Expected: PASS (all tests in file)

- [ ] **Step 6: Regenerate frontend types**

Run: `make dev` (backend running) then `cd frontend && npm run gen:api`

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/documents.py backend/app/schemas/document.py backend/tests/test_documents_api.py frontend/src/api/schema.ts
git commit -m "feat(replace): add POST /documents/{id}/replace endpoint"
```

### Task 18: Frontend Replace action

**Files:**
- Modify: `frontend/src/api/hooks/useDocuments.ts`
- Modify: `frontend/src/components/documents/DocumentRow.tsx`
- Modify: `frontend/tests/documents.test.tsx`

**Interfaces:**
- Consumes: `POST /documents/{document_id}/replace` (Task 17).
- Produces: `useReplaceDocument(): UseMutationResult<ReplaceDocumentResponse, UploadError, { documentId: string; file: File }>`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/documents.test.tsx — add (match this file's existing upload-test
// MSW/user-event conventions):
it("replaces a document via the Replace action and shows a toast on success", async () => {
  server.use(
    http.post("*/documents/:id/replace", () =>
      HttpResponse.json({ document: { id: "1", status: "processing", /* ... */ }, no_changes: false }),
    ),
  )
  render(<DocumentList />, { wrapper: Providers })

  await userEvent.click(await screen.findByRole("button", { name: /replace/i }))
  const input = screen.getByLabelText(/choose file/i) // or however the existing upload dropzone exposes its file input
  await userEvent.upload(input, new File(["new content"], "updated.pdf", { type: "application/pdf" }))

  expect(await screen.findByText(/processing/i)).toBeInTheDocument()
})
```

Adjust selectors to match whatever accessible names `DocumentRow`/the upload dropzone already use elsewhere in this test file — don't invent new ones.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- documents.test.tsx`
Expected: FAIL — no "Replace" button exists yet.

- [ ] **Step 3: Add the hook**

```typescript
// frontend/src/api/hooks/useDocuments.ts — add:
type ReplaceDocumentResponse = components["schemas"]["ReplaceDocumentResponse"]

export interface ReplaceDocumentInput {
  documentId: string
  file: File
}

export function useReplaceDocument(): UseMutationResult<ReplaceDocumentResponse, UploadError, ReplaceDocumentInput> {
  const queryClient = useQueryClient()
  const documentsListKey = getDocumentsListKey()

  return useMutation<ReplaceDocumentResponse, UploadError, ReplaceDocumentInput>({
    mutationFn: async ({ documentId, file }) => {
      const formData = new FormData()
      formData.append("file", file)

      const { data, error, response } = await fetchClient.POST("/documents/{document_id}/replace", {
        params: { path: { document_id: documentId } },
        body: formData as unknown as never,
        bodySerializer: (body) => body as unknown as BodyInit,
      })

      if (error || !data) {
        const detail =
          error && typeof error === "object" && "detail" in error ? String(error.detail) : "Replace failed"
        throw new UploadError(response.status, detail)
      }
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsListKey })
    },
  })
}
```

- [ ] **Step 4: Add the Replace action to `DocumentRow`**

```tsx
// frontend/src/components/documents/DocumentRow.tsx — add alongside the existing
// delete button/dialog, following this file's existing pattern for a file-input-
// backed action (mirror UploadDropzone's <label>-wrapped-<input> pattern, NOT a
// <button>-wraps-<input>, to avoid the same double-file-dialog re-entrancy bug
# fixed there during Phase 4):
import { useReplaceDocument } from "@/api/hooks/useDocuments"
import { toast } from "sonner"

// inside the component:
const replaceDocument = useReplaceDocument()

function handleReplaceFileSelected(file: File) {
  replaceDocument.mutate(
    { documentId: document.id, file },
    {
      onSuccess: (result) => {
        toast.success(result.no_changes ? "No changes detected" : "Replacing document…")
      },
      onError: (error) => {
        toast.error(error.message)
      },
    },
  )
}

// in the JSX, alongside the delete button:
<label className="cursor-pointer text-sm underline">
  Replace
  <input
    type="file"
    className="hidden"
    onChange={(e) => {
      const file = e.target.files?.[0]
      if (file) handleReplaceFileSelected(file)
      e.target.value = "" // allow re-selecting the same file next time
    }}
  />
</label>
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm run test -- documents.test.tsx`
Expected: PASS

- [ ] **Step 6: Run the full frontend suite, typecheck, lint, build**

Run: `cd frontend && npm run test && npm run typecheck && npm run lint && npm run build`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/hooks/useDocuments.ts frontend/src/components/documents/DocumentRow.tsx frontend/tests/documents.test.tsx
git commit -m "feat(replace): add Replace action to the document list UI"
```

**Milestone 3 checkpoint (and end of this plan):** Upload a document, then use "Replace" with a slightly edited version of the same file. Confirm via the UI that it flips to "Processing…" then "ready", and — if you can inspect the DB — confirm that chunks whose text didn't change kept their original row `id` (not re-inserted), while only the edited portion got new rows with fresh embeddings. Also test replacing with the *exact same* file and confirm it short-circuits instantly with "No changes detected."

---

## Notes for Phase 5 (not part of this plan)

- The `procrastinate` worker needs to run as its own process in production — a second service/container alongside the API in whatever Docker Compose setup Phase 5 builds (`uv run procrastinate --app=app.jobs.app.app worker`).
- `fastembed`'s ONNX model and `pymupdf4llm` both download/cache assets on first use — worth pre-warming the Docker image or documenting the first-run delay.
