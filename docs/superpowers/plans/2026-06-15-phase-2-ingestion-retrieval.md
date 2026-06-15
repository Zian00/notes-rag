# Phase 2 — Ingestion + Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an authenticated user upload lecture files (PDF/PPTX/DOCX/TXT/MD/PNG/JPG, incl. scanned PDFs via OCR), have them parsed → structure-aware chunked → embedded (Gemini, 1536-dim) → stored in pgvector scoped to their account, and run a plain semantic `/search` over their own chunks plus manage documents (list/delete).

**Architecture:** Layered + hexagonal, matching Phases 0–1. New ports (ABCs) `StorageBackend`, `DocumentParser`, `OcrProvider`, `EmbeddingsProvider` live in `app/rag/`; concrete adapters are injected via FastAPI `Depends`. `IngestionService` owns an atomic transaction with compensating file cleanup; `RetrievalService` does the query embed + pgvector search. Vector access lives in `ChunkRepository` (no separate VectorStore port). All rows are per-user scoped.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Postgres + pgvector (`pgvector.sqlalchemy.Vector`, HNSW cosine), `google-genai` (embeddings), `pypdf` + `pdf2image` + `pytesseract` (Tesseract OCR), `python-pptx`, `python-docx`, `Pillow`, `langchain-text-splitters` + `tiktoken` (chunking). Tests: pytest + pytest-asyncio + httpx against the dedicated `notes_rag_test` DB, with fake embeddings/OCR adapters.

**Spec:** `docs/superpowers/specs/2026-06-15-ingestion-retrieval-design.md`

> **Commit policy (user standing rule):** NEVER auto-commit. Each task lists a commit step, but the executor must STOP at each **milestone boundary** and let the user run the commit themselves. Group commits per milestone unless the user says otherwise.

---

## File Structure

**New runtime modules**
- `app/rag/__init__.py` — package marker
- `app/rag/types.py` — `Segment`, `ParsedDocument`, `Chunk` dataclasses (pure value objects)
- `app/rag/storage.py` — `StorageBackend` ABC + `LocalFileStorage`
- `app/rag/ocr.py` — `OcrProvider` ABC + `TesseractOcr`
- `app/rag/embeddings.py` — `EmbeddingsProvider` ABC + `GeminiEmbeddingsProvider` + `l2_normalize`
- `app/rag/chunking.py` — `Chunker` (structure-aware recursive token splitting)
- `app/rag/parsing/__init__.py` — `DocumentParser` ABC + `ParserDispatcher` (selects adapter by content type)
- `app/rag/parsing/text.py` — `TextParser` (TXT + Markdown-heading segments)
- `app/rag/parsing/office.py` — `PptxParser`, `DocxParser`
- `app/rag/parsing/pdf.py` — `PdfParser` (pypdf + per-page OCR fallback)
- `app/rag/parsing/image.py` — `ImageParser` (single OCR segment)
- `app/utils/files.py` — `sniff_content_type`, `sanitize_filename`
- `app/models/document.py` — `Document`, `DocumentChunk` ORM models
- `app/db/repositories/document.py` — `DocumentRepository`
- `app/db/repositories/chunk.py` — `ChunkRepository`
- `app/services/ingestion.py` — `IngestionService` + domain errors
- `app/services/retrieval.py` — `RetrievalService`
- `app/schemas/document.py` — `DocumentResponse`, `SearchRequest`, `ChunkMatch`, `DuplicateDocumentResponse`
- `app/api/documents.py` — documents router (upload/list/delete)
- `app/api/search.py` — search router
- `app/db/migrations/versions/0003_documents_chunks.py` — migration

**Modified**
- `app/core/config.py` — new Settings fields
- `app/models/__init__.py` — register new models
- `app/api/deps.py` — provider + service dependencies
- `app/main.py` — register `documents` + `search` routers
- `pyproject.toml` — new deps
- `backend/Dockerfile`, `docker-compose.yml` — tesseract/poppler + uploads volume
- `tests/conftest.py` — truncate list, fakes, temp-storage overrides, `auth_client` fixture

**New test modules**
- `tests/fakes.py` — `FakeEmbeddingsProvider`, `FakeOcrProvider`, fixture builders
- `tests/test_files_util.py`, `tests/test_chunking.py`, `tests/test_parsing.py`,
  `tests/test_storage.py`, `tests/test_embeddings.py`, `tests/test_document_repository.py`,
  `tests/test_chunk_repository.py`, `tests/test_ingestion_service.py`,
  `tests/test_retrieval_service.py`, `tests/test_documents_api.py`, `tests/test_search_api.py`

---

## Milestone A — Config, dependencies, data model, migration

### Task 1: Add dependencies

**Files:** Modify `backend/pyproject.toml`

- [ ] **Step 1: Add runtime + dev deps via uv**

Run (from `backend/`):
```bash
cd backend
uv add "pypdf>=5.1" "pdf2image>=1.17" "pytesseract>=0.3.13" "Pillow>=11.0" \
       "python-pptx>=1.0" "python-docx>=1.1" "langchain-text-splitters>=0.3" \
       "tiktoken>=0.8" "google-genai>=1.0"
uv add --dev "fpdf2>=2.8"
```
Expected: `uv.lock` updates; `pyproject.toml` `dependencies` gains the 9 runtime packages and `dev` gains `fpdf2`.

- [ ] **Step 2: Verify install**

Run: `cd backend && uv run python -c "import pypdf, pdf2image, pytesseract, PIL, pptx, docx, tiktoken, langchain_text_splitters, google.genai; print('ok')"`
Expected: prints `ok` (no ImportError). *(pdf2image importing is fine even without poppler installed; poppler is only needed at call time.)*

- [ ] **Step 3: Commit**
```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "build: add Phase 2 ingestion/OCR/embeddings dependencies"
```

---

### Task 2: Extend Settings

**Files:** Modify `app/core/config.py`; Test `tests/test_config_phase2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_phase2.py`:
```python
from app.core.config import Settings


def _settings(**over: object) -> Settings:
    base = {"database_url": "postgresql+asyncpg://u:p@localhost/db", "jwt_secret": "x"}
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def test_phase2_defaults():
    s = _settings()
    assert s.embedding_model == "gemini-embedding-001"
    assert s.embedding_dimension == 1536
    assert s.embedding_doc_task_type == "RETRIEVAL_DOCUMENT"
    assert s.embedding_query_task_type == "RETRIEVAL_QUERY"
    assert s.chunk_tokens == 512
    assert s.chunk_overlap_tokens == 64
    assert s.upload_dir == "./uploads"
    assert s.max_upload_bytes == 26_214_400
    assert s.ocr_enabled is True
    assert s.ocr_language == "eng"
    assert s.pdf_ocr_min_chars_per_page == 10
    assert s.tesseract_cmd is None
    assert s.retrieval_top_k == 5
    assert "application/pdf" in s.allowed_content_types
    assert "image/png" in s.allowed_content_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_config_phase2.py -v`
Expected: FAIL (`AttributeError`/validation — fields don't exist yet).

- [ ] **Step 3: Add the fields**

In `app/core/config.py`, inside `Settings`, after the existing `cors_origins` line add:
```python
    # Embeddings (Phase 2)
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 1536
    embedding_doc_task_type: str = "RETRIEVAL_DOCUMENT"
    embedding_query_task_type: str = "RETRIEVAL_QUERY"

    # Chunking
    chunk_tokens: int = 512
    chunk_overlap_tokens: int = 64

    # Uploads / storage
    upload_dir: str = "./uploads"
    max_upload_bytes: int = 26_214_400  # 25 MiB
    allowed_content_types: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
        "image/png",
        "image/jpeg",
    ]

    # OCR
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    pdf_ocr_min_chars_per_page: int = 10
    tesseract_cmd: str | None = None

    # Retrieval
    retrieval_top_k: int = 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_config_phase2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/core/config.py backend/tests/test_config_phase2.py
git commit -m "feat: add Phase 2 ingestion/retrieval settings"
```

---

### Task 3: Document + DocumentChunk models

**Files:** Create `app/models/document.py`; Modify `app/models/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_document_models.py`:
```python
import uuid

from app.models.document import Document, DocumentChunk


def test_document_defaults():
    doc = Document(
        user_id=uuid.uuid4(),
        filename="lecture3.pdf",
        content_type="application/pdf",
        content_hash="abc",
        storage_path="/tmp/x",
        file_size=10,
        chunk_count=0,
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )
    assert doc.title is None
    assert doc.tags == []
    assert doc.page_count is None


def test_chunk_fields():
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        chunk_index=0,
        content="hello",
        embedding=[0.0] * 1536,
    )
    assert chunk.chunk_index == 0
    assert chunk.section is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_document_models.py -v`
Expected: FAIL (`ModuleNotFoundError: app.models.document`).

- [ ] **Step 3: Create the models**

Create `app/models/document.py`:
```python
import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Must match Settings.embedding_dimension; the migration hardcodes the same value.
EMBEDDING_DIM = 1536


def _now() -> datetime:
    return datetime.now(tz=UTC)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    course: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    content_type: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(1024))
    file_size: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(128))
    embedding_dimension: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(default=_now)
```

Note: `created_at`/`updated_at` use `Mapped[datetime]` with timezone via SQLAlchemy default mapping — to match the existing explicit style, use `DateTime(timezone=True)`. Replace the two `created_at`/`updated_at` lines in each class with the explicit form used by `app/models/user.py`:
```python
from sqlalchemy import DateTime
# ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
```
(Document gets both; DocumentChunk gets only `created_at`.)

- [ ] **Step 4: Register models**

Replace `app/models/__init__.py` with:
```python
from app.models.document import Document, DocumentChunk
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = ["Document", "DocumentChunk", "RefreshToken", "User"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_document_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
git add backend/app/models/document.py backend/app/models/__init__.py backend/tests/test_document_models.py
git commit -m "feat: add Document and DocumentChunk models"
```

---

### Task 4: Alembic migration for documents + chunks

**Files:** Create `app/db/migrations/versions/0003_documents_chunks.py`

- [ ] **Step 1: Write the migration**

Create `app/db/migrations/versions/0003_documents_chunks.py`:
```python
"""create documents and document_chunks

Revision ID: 0003_documents_chunks
Revises: 0002_users_refresh_tokens
Create Date: 2026-06-15
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_documents_chunks"
down_revision = "0002_users_refresh_tokens"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("course", sa.String(length=256), nullable=True),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index(
        "uq_documents_user_content_hash",
        "documents",
        ["user_id", "content_hash"],
        unique=True,
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "document_id",
            sa.UUID(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=512), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_user_id", "document_chunks", ["user_id"])
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("documents")
```

- [ ] **Step 2: Apply the migration to the dev DB**

Run: `make db` (ensure Postgres up), then `cd backend && uv run alembic upgrade head`
Expected: ends at `0003_documents_chunks`, no errors.

- [ ] **Step 3: Verify tables + HNSW index exist**

Run: `docker compose exec postgres psql -U notes -d notes_rag -c "\d document_chunks"`
Expected: shows the `embedding vector(1536)` column and an `hnsw` index using `vector_cosine_ops`.

- [ ] **Step 4: Commit**
```bash
git add backend/app/db/migrations/versions/0003_documents_chunks.py
git commit -m "feat: migration for documents and document_chunks (pgvector HNSW)"
```

---

### Task 5: Update test isolation (truncate list)

**Files:** Modify `tests/conftest.py`

- [ ] **Step 1: Extend the truncate list**

In `tests/conftest.py`, change:
```python
_TRUNCATE_TABLES = "users, refresh_tokens"
```
to:
```python
_TRUNCATE_TABLES = "users, refresh_tokens, documents, document_chunks"
```
(`RESTART IDENTITY CASCADE` already handles dependents; listing them is explicit + clear.)

- [ ] **Step 2: Run the existing suite to confirm nothing breaks**

Run: `make test-db` then `cd backend && uv run pytest -q`
Expected: existing Phase 0/1 tests still PASS (new tables now created + truncated each test).

- [ ] **Step 3: Commit**
```bash
git add backend/tests/conftest.py
git commit -m "test: include Phase 2 tables in test truncation"
```

> **MILESTONE A BOUNDARY — STOP. Ask the user to review + commit before continuing.**

---

## Milestone B — Value types, file utils, chunking, parsers, providers (pure, no DB)

### Task 6: RAG value types

**Files:** Create `app/rag/__init__.py`, `app/rag/types.py`; Test `tests/test_rag_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rag_types.py`:
```python
from app.rag.types import Chunk, ParsedDocument, Segment


def test_parsed_document_holds_segments():
    seg = Segment(text="hello world", page_number=1, section="Intro")
    doc = ParsedDocument(segments=[seg], page_count=1)
    assert doc.segments[0].text == "hello world"
    assert doc.page_count == 1


def test_chunk_carries_segment_metadata():
    c = Chunk(content="hi", chunk_index=0, page_number=2, section="Topic", token_count=1)
    assert c.page_number == 2 and c.section == "Topic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_rag_types.py -v`
Expected: FAIL (`ModuleNotFoundError: app.rag`).

- [ ] **Step 3: Create the package + types**

Create `app/rag/__init__.py` (empty).
Create `app/rag/types.py`:
```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Segment:
    """A structural unit of a parsed document (a page, slide, or heading block)."""

    text: str
    page_number: int | None = None
    section: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """Output of a DocumentParser: ordered segments + page/slide count."""

    segments: list[Segment] = field(default_factory=list)
    page_count: int | None = None


@dataclass(frozen=True)
class Chunk:
    """A chunk ready to embed and persist."""

    content: str
    chunk_index: int
    page_number: int | None = None
    section: str | None = None
    token_count: int | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_rag_types.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/rag/__init__.py backend/app/rag/types.py backend/tests/test_rag_types.py
git commit -m "feat: RAG value types (Segment, ParsedDocument, Chunk)"
```

---

### Task 7: File utils — content sniffing + filename sanitization

**Files:** Create `app/utils/files.py`; Test `tests/test_files_util.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_files_util.py`:
```python
import io
import zipfile

from app.utils.files import sanitize_filename, sniff_content_type

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF_MAGIC = b"%PDF-1.7\n" + b"rest"


def _ooxml(marker_dir: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(f"{marker_dir}/presentation.xml", "x")
    return buf.getvalue()


def test_sniff_pdf_png_jpeg():
    assert sniff_content_type("a.pdf", PDF_MAGIC) == "application/pdf"
    assert sniff_content_type("a.png", PNG_MAGIC) == "image/png"
    assert sniff_content_type("a.jpg", JPEG_MAGIC) == "image/jpeg"


def test_sniff_pptx_vs_docx():
    pptx = sniff_content_type("a.pptx", _ooxml("ppt"))
    docx = sniff_content_type("a.docx", _ooxml("word"))
    assert pptx.endswith("presentationml.presentation")
    assert docx.endswith("wordprocessingml.document")


def test_sniff_text_and_markdown_by_extension():
    assert sniff_content_type("notes.txt", b"plain text") == "text/plain"
    assert sniff_content_type("notes.md", b"# Heading") == "text/markdown"


def test_sniff_rejects_unknown():
    assert sniff_content_type("evil.exe", b"MZ\x90\x00") is None
    # A .txt that is actually binary (not utf-8) is rejected.
    assert sniff_content_type("fake.txt", b"\xff\xfe\x00\x01") is None


def test_sanitize_filename_strips_paths_and_bad_chars():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("my notes/Lecture 3.pdf") == "Lecture 3.pdf"
    assert sanitize_filename("") == "upload"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_files_util.py -v`
Expected: FAIL (`ModuleNotFoundError: app.utils.files`).

- [ ] **Step 3: Implement**

Create `app/utils/files.py`:
```python
import io
import os
import zipfile

_PDF = "application/pdf"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TXT = "text/plain"
_MD = "text/markdown"
_PNG = "image/png"
_JPEG = "image/jpeg"


def sniff_content_type(filename: str, data: bytes) -> str | None:
    """Determine the real content type from magic bytes (not the client's label).

    Returns a canonical MIME string, or None if unsupported. OOXML files (PPTX/DOCX)
    are ZIP archives, so we inspect the archive entries to tell them apart. TXT/MD have
    no magic number, so we accept them by extension only if the bytes decode as UTF-8.
    """
    if data.startswith(b"%PDF"):
        return _PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _PNG
    if data.startswith(b"\xff\xd8\xff"):
        return _JPEG
    if data.startswith(b"PK\x03\x04"):
        return _sniff_ooxml(data)

    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return _MD if ext == ".md" else _TXT
    return None


def _sniff_ooxml(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return None
    if any(n.startswith("ppt/") for n in names):
        return _PPTX
    if any(n.startswith("word/") for n in names):
        return _DOCX
    return None


def sanitize_filename(filename: str) -> str:
    """Strip directory components and return a safe base name (fallback 'upload')."""
    base = os.path.basename(filename.replace("\\", "/")).strip()
    return base or "upload"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_files_util.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/utils/files.py backend/tests/test_files_util.py
git commit -m "feat: content sniffing + filename sanitization"
```

---

### Task 8: Chunker (structure-aware recursive token splitting)

**Files:** Create `app/rag/chunking.py`; Test `tests/test_chunking.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chunking.py`:
```python
from app.rag.chunking import Chunker
from app.rag.types import ParsedDocument, Segment


def _chunker(tokens: int = 30, overlap: int = 5) -> Chunker:
    return Chunker(chunk_tokens=tokens, chunk_overlap_tokens=overlap)


def test_small_segment_stays_one_chunk_and_keeps_metadata():
    doc = ParsedDocument(segments=[Segment("short text", page_number=2, section="Intro")])
    chunks = _chunker().split(doc)
    assert len(chunks) == 1
    assert chunks[0].page_number == 2
    assert chunks[0].section == "Intro"
    assert chunks[0].chunk_index == 0
    assert (chunks[0].token_count or 0) > 0


def test_never_merges_across_segments():
    doc = ParsedDocument(
        segments=[
            Segment("alpha beta", page_number=1, section="A"),
            Segment("gamma delta", page_number=2, section="B"),
        ]
    )
    chunks = _chunker().split(doc)
    # Each short segment becomes its own chunk; no chunk mixes page 1 and page 2 text.
    by_page = {c.page_number for c in chunks}
    assert by_page == {1, 2}
    for c in chunks:
        if c.page_number == 1:
            assert "gamma" not in c.content
        if c.page_number == 2:
            assert "alpha" not in c.content


def test_large_segment_is_split_with_increasing_indexes():
    big = " ".join(f"word{i}" for i in range(300))
    doc = ParsedDocument(segments=[Segment(big, page_number=1, section="Big")])
    chunks = _chunker(tokens=30, overlap=5).split(doc)
    assert len(chunks) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.section == "Big" for c in chunks)


def test_blank_segments_are_skipped():
    doc = ParsedDocument(segments=[Segment("   ", page_number=1), Segment("real", page_number=2)])
    chunks = _chunker().split(doc)
    assert len(chunks) == 1
    assert chunks[0].page_number == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: FAIL (`ModuleNotFoundError: app.rag.chunking`).

- [ ] **Step 3: Implement**

Create `app/rag/chunking.py`:
```python
import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.types import Chunk, ParsedDocument

# tiktoken is only an approximation of Gemini's tokenizer; we use it solely to size
# chunks consistently (exactness is not required).
_ENCODING = "cl100k_base"


class Chunker:
    """Splits a ParsedDocument into chunks, recursively by token count, WITHIN each
    segment (never merging across slides/pages). Each chunk inherits its segment's
    page_number/section metadata."""

    def __init__(self, chunk_tokens: int, chunk_overlap_tokens: int) -> None:
        self._encoder = tiktoken.get_encoding(_ENCODING)
        self._splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=_ENCODING,
            chunk_size=chunk_tokens,
            chunk_overlap=chunk_overlap_tokens,
        )

    def split(self, document: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        index = 0
        for segment in document.segments:
            if not segment.text.strip():
                continue
            for piece in self._splitter.split_text(segment.text):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/rag/chunking.py backend/tests/test_chunking.py
git commit -m "feat: structure-aware token Chunker"
```

---

### Task 9: OcrProvider port + Tesseract adapter + Fake

**Files:** Create `app/rag/ocr.py`, `tests/fakes.py`; Test `tests/test_ocr.py`

- [ ] **Step 1: Write the failing test**

Create `tests/fakes.py`:
```python
from PIL import Image

from app.rag.embeddings import EmbeddingsProvider
from app.rag.ocr import OcrProvider


class FakeOcrProvider(OcrProvider):
    """Returns a fixed string, ignoring the image — deterministic, no Tesseract."""

    def __init__(self, text: str = "ocr text") -> None:
        self._text = text

    def extract_text(self, image: Image.Image) -> str:
        return self._text


class FakeEmbeddingsProvider(EmbeddingsProvider):
    """Deterministic unit vectors derived from text length — no network/key.

    Vector i is a one-hot at position (len(text) % dim), so different-length texts
    sort deterministically by cosine distance.
    """

    def __init__(self, dimension: int = 1536) -> None:
        self._dim = dimension

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self._dim
        v[len(text) % self._dim] = 1.0
        return v

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
```

Create `tests/test_ocr.py`:
```python
import shutil

import pytest
from PIL import Image, ImageDraw

from app.rag.ocr import TesseractOcr


def test_tesseract_reads_generated_text_image():
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not installed")
    img = Image.new("RGB", (220, 60), "white")
    ImageDraw.Draw(img).text((10, 20), "HELLO", fill="black")
    text = TesseractOcr(language="eng").extract_text(img)
    assert "HELLO" in text.upper()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ocr.py -v`
Expected: FAIL (`ModuleNotFoundError: app.rag.ocr`). *(If tesseract is absent the test would skip once the import resolves — the failure here is the missing module.)*

- [ ] **Step 3: Implement**

Create `app/rag/ocr.py`:
```python
from abc import ABC, abstractmethod

import pytesseract
from PIL import Image


class OcrProvider(ABC):
    """Port: extract text from a single image."""

    @abstractmethod
    def extract_text(self, image: Image.Image) -> str: ...


class TesseractOcr(OcrProvider):
    """Local Tesseract OCR adapter. Requires the `tesseract` binary on the host."""

    def __init__(self, language: str = "eng", cmd: str | None = None) -> None:
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        self._language = language

    def extract_text(self, image: Image.Image) -> str:
        return pytesseract.image_to_string(image, lang=self._language)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_ocr.py -v`
Expected: PASS (or SKIP if tesseract not installed locally — both are green).

- [ ] **Step 5: Commit**
```bash
git add backend/app/rag/ocr.py backend/tests/fakes.py backend/tests/test_ocr.py
git commit -m "feat: OcrProvider port + Tesseract adapter + fakes"
```

---

### Task 10: EmbeddingsProvider port + Gemini adapter + normalization

**Files:** Create `app/rag/embeddings.py`; Test `tests/test_embeddings.py`

**Docs to check:** google-genai embeddings — `client.models.embed_content(model, contents, config=types.EmbedContentConfig(task_type=..., output_dimensionality=...))`; use context7 (`/googleapis/python-genai`) if the call signature has drifted.

- [ ] **Step 1: Write the failing test**

Create `tests/test_embeddings.py`:
```python
import math

from app.rag.embeddings import l2_normalize


def test_l2_normalize_unit_length():
    out = l2_normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0, rel_tol=1e-6)
    assert math.isclose(out[0], 0.6, rel_tol=1e-6)


def test_l2_normalize_zero_vector_is_safe():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_embeddings.py -v`
Expected: FAIL (`ModuleNotFoundError: app.rag.embeddings`).

- [ ] **Step 3: Implement**

Create `app/rag/embeddings.py`:
```python
import math
from abc import ABC, abstractmethod

from google import genai
from google.genai import types

from app.core.config import Settings


def l2_normalize(vector: list[float]) -> list[float]:
    """Scale to unit length. Required because Gemini only normalizes the full
    3072-dim output; truncated dims (1536) must be normalized for cosine to be valid."""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return vector
    return [x / norm for x in vector]


class EmbeddingsProvider(ABC):
    """Port: turn text into embedding vectors."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


class GeminiEmbeddingsProvider(EmbeddingsProvider):
    """Gemini embeddings via google-genai. Document and query use asymmetric task types."""

    def __init__(self, settings: Settings) -> None:
        self._client = genai.Client(api_key=settings.google_api_key)
        self._model = settings.embedding_model
        self._dim = settings.embedding_dimension
        self._doc_task = settings.embedding_doc_task_type
        self._query_task = settings.embedding_query_task_type

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        resp = self._client.models.embed_content(
            model=self._model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=self._dim
            ),
        )
        return [l2_normalize(list(e.values)) for e in resp.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(texts, self._doc_task)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], self._query_task)[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_embeddings.py -v`
Expected: PASS. *(The Gemini adapter itself is exercised only via mocks/live; the pure `l2_normalize` is what's unit-tested here.)*

- [ ] **Step 5: Commit**
```bash
git add backend/app/rag/embeddings.py backend/tests/test_embeddings.py
git commit -m "feat: EmbeddingsProvider port + Gemini adapter + L2 normalize"
```

---

### Task 11: StorageBackend port + LocalFileStorage

**Files:** Create `app/rag/storage.py`; Test `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:
```python
import uuid
from pathlib import Path

from app.rag.storage import LocalFileStorage


def test_save_writes_file_under_user_dir(tmp_path: Path):
    storage = LocalFileStorage(str(tmp_path))
    user_id = uuid.uuid4()
    path = storage.save(user_id, "Lecture 3.pdf", b"data")
    p = Path(path)
    assert p.exists()
    assert p.read_bytes() == b"data"
    assert str(user_id) in path
    assert p.name.endswith("Lecture 3.pdf")


def test_delete_removes_file_and_is_idempotent(tmp_path: Path):
    storage = LocalFileStorage(str(tmp_path))
    path = storage.save(uuid.uuid4(), "x.txt", b"hi")
    storage.delete(path)
    assert not Path(path).exists()
    storage.delete(path)  # second delete must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_storage.py -v`
Expected: FAIL (`ModuleNotFoundError: app.rag.storage`).

- [ ] **Step 3: Implement**

Create `app/rag/storage.py`:
```python
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.utils.files import sanitize_filename


class StorageBackend(ABC):
    """Port: persist and remove the original uploaded file."""

    @abstractmethod
    def save(self, user_id: uuid.UUID, filename: str, data: bytes) -> str: ...

    @abstractmethod
    def delete(self, path: str) -> None: ...


class LocalFileStorage(StorageBackend):
    """Writes files under UPLOAD_DIR/{user_id}/{uuid}_{safe_name}."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def save(self, user_id: uuid.UUID, filename: str, data: bytes) -> str:
        safe = sanitize_filename(filename)
        user_dir = self._root / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        dest = user_dir / f"{uuid.uuid4().hex}_{safe}"
        dest.write_bytes(data)
        return str(dest)

    def delete(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_storage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/rag/storage.py backend/tests/test_storage.py
git commit -m "feat: StorageBackend port + LocalFileStorage"
```

---

### Task 12: DocumentParser port + Text/Office/Image/PDF adapters + dispatcher

**Files:** Create `app/rag/parsing/__init__.py`, `app/rag/parsing/text.py`, `app/rag/parsing/office.py`, `app/rag/parsing/image.py`, `app/rag/parsing/pdf.py`; Test `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_parsing.py`:
```python
import io

from docx import Document as DocxDocument
from fpdf import FPDF
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app.rag.parsing import ParserDispatcher
from app.rag.parsing.image import ImageParser
from app.rag.parsing.office import DocxParser, PptxParser
from app.rag.parsing.pdf import PdfParser
from app.rag.parsing.text import TextParser
from tests.fakes import FakeOcrProvider


def test_text_parser_plain_is_single_segment():
    doc = TextParser().parse(b"line one\nline two", "text/plain")
    assert len(doc.segments) == 1
    assert "line one" in doc.segments[0].text


def test_text_parser_markdown_splits_on_headings():
    md = b"# Intro\nhello\n## Details\nworld"
    doc = TextParser().parse(md, "text/markdown")
    sections = [s.section for s in doc.segments]
    assert "Intro" in sections
    assert any(s and "Details" in s for s in sections)


def test_pptx_parser_one_segment_per_slide():
    prs = Presentation()
    for i in range(2):
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = f"Slide {i}"
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = f"body {i}"
    buf = io.BytesIO()
    prs.save(buf)
    doc = PptxParser().parse(buf.getvalue(), "application/...")
    assert len(doc.segments) == 2
    assert doc.segments[0].page_number == 1
    assert doc.segments[0].section == "Slide 0"
    assert "body 0" in doc.segments[0].text


def test_docx_parser_extracts_text():
    d = DocxDocument()
    d.add_paragraph("first para")
    d.add_paragraph("second para")
    buf = io.BytesIO()
    d.save(buf)
    doc = DocxParser().parse(buf.getvalue(), "application/...")
    joined = " ".join(s.text for s in doc.segments)
    assert "first para" in joined and "second para" in joined


def test_image_parser_uses_ocr():
    img = Image.new("RGB", (40, 20), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = ImageParser(FakeOcrProvider("scanned words")).parse(buf.getvalue(), "image/png")
    assert len(doc.segments) == 1
    assert doc.segments[0].text == "scanned words"
    assert doc.segments[0].page_number == 1


def test_pdf_parser_extracts_text_layer():
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.cell(text="hello pdf world")
    data = bytes(pdf.output())
    doc = PdfParser(ocr=FakeOcrProvider("FALLBACK"), ocr_enabled=True, min_chars=5).parse(
        data, "application/pdf"
    )
    text = " ".join(s.text for s in doc.segments)
    assert "hello pdf world" in text
    assert "FALLBACK" not in text  # text layer present -> no OCR
    assert doc.page_count == 1


def test_pdf_parser_falls_back_to_ocr_for_image_only_page():
    # An image saved as a 1-page PDF has no text layer -> OCR fallback fires.
    img = Image.new("RGB", (200, 80), "white")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    doc = PdfParser(ocr=FakeOcrProvider("FALLBACK TEXT"), ocr_enabled=True, min_chars=5).parse(
        buf.getvalue(), "application/pdf"
    )
    text = " ".join(s.text for s in doc.segments)
    assert "FALLBACK TEXT" in text


def test_dispatcher_routes_by_content_type():
    dispatcher = ParserDispatcher(ocr=FakeOcrProvider(), ocr_enabled=True, min_chars=5)
    doc = dispatcher.parse(b"plain words", "text/plain")
    assert "plain words" in doc.segments[0].text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_parsing.py -v`
Expected: FAIL (`ModuleNotFoundError: app.rag.parsing`).

- [ ] **Step 3: Implement the parsers**

Create `app/rag/parsing/text.py`:
```python
from app.rag.types import ParsedDocument, Segment


class TextParser:
    """TXT → one segment. Markdown → one segment per heading block (section = heading)."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        text = data.decode("utf-8", errors="replace")
        if content_type != "text/markdown":
            return ParsedDocument(segments=[Segment(text=text)], page_count=None)
        return ParsedDocument(segments=_split_markdown(text), page_count=None)


def _split_markdown(text: str) -> list[Segment]:
    segments: list[Segment] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or current_heading:
            segments.append(Segment(text=body, section=current_heading))

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            flush()
            buffer = []
            current_heading = stripped.lstrip("#").strip()
        else:
            buffer.append(line)
    flush()
    return segments or [Segment(text=text)]
```

Create `app/rag/parsing/office.py`:
```python
import io

from docx import Document as DocxDocument
from pptx import Presentation

from app.rag.types import ParsedDocument, Segment


class PptxParser:
    """One segment per slide; section = slide title, page_number = slide number."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        prs = Presentation(io.BytesIO(data))
        segments: list[Segment] = []
        for index, slide in enumerate(prs.slides, start=1):
            title: str | None = None
            texts: list[str] = []
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                frame_text = shape.text_frame.text
                if shape == slide.shapes.title and frame_text:
                    title = frame_text
                if frame_text:
                    texts.append(frame_text)
            segments.append(
                Segment(text="\n".join(texts), page_number=index, section=title)
            )
        return ParsedDocument(segments=segments, page_count=len(segments))


class DocxParser:
    """Segments split on heading-styled paragraphs; else a single segment."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        doc = DocxDocument(io.BytesIO(data))
        segments: list[Segment] = []
        current_heading: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            body = "\n".join(buffer).strip()
            if body or current_heading:
                segments.append(Segment(text=body, section=current_heading))

        for para in doc.paragraphs:
            style = (para.style.name or "").lower() if para.style else ""
            if style.startswith("heading") and para.text.strip():
                flush()
                buffer = []
                current_heading = para.text.strip()
            elif para.text.strip():
                buffer.append(para.text)
        flush()
        if not segments:
            segments = [Segment(text="")]
        return ParsedDocument(segments=segments, page_count=None)
```

Create `app/rag/parsing/image.py`:
```python
import io

from PIL import Image

from app.rag.ocr import OcrProvider
from app.rag.types import ParsedDocument, Segment


class ImageParser:
    """Runs the whole image through OCR as a single segment (page_number = 1)."""

    def __init__(self, ocr: OcrProvider) -> None:
        self._ocr = ocr

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        image = Image.open(io.BytesIO(data))
        text = self._ocr.extract_text(image)
        return ParsedDocument(segments=[Segment(text=text, page_number=1)], page_count=1)
```

Create `app/rag/parsing/pdf.py`:
```python
import io

from pdf2image import convert_from_bytes
from pypdf import PdfReader

from app.rag.ocr import OcrProvider
from app.rag.types import ParsedDocument, Segment


class PdfParser:
    """Extracts text per page with pypdf; any page below `min_chars` is rasterized
    (pdf2image, requires poppler) and OCR'd. Handles scanned + mixed PDFs."""

    def __init__(self, ocr: OcrProvider, ocr_enabled: bool, min_chars: int) -> None:
        self._ocr = ocr
        self._ocr_enabled = ocr_enabled
        self._min_chars = min_chars

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        reader = PdfReader(io.BytesIO(data))
        segments: list[Segment] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if len(text) < self._min_chars and self._ocr_enabled:
                text = self._ocr_page(data, index).strip() or text
            segments.append(Segment(text=text, page_number=index))
        return ParsedDocument(segments=segments, page_count=len(reader.pages))

    def _ocr_page(self, data: bytes, page_number: int) -> str:
        images = convert_from_bytes(data, first_page=page_number, last_page=page_number)
        if not images:
            return ""
        return self._ocr.extract_text(images[0])
```

Create `app/rag/parsing/__init__.py`:
```python
from app.rag.ocr import OcrProvider
from app.rag.parsing.image import ImageParser
from app.rag.parsing.office import DocxParser, PptxParser
from app.rag.parsing.pdf import PdfParser
from app.rag.parsing.text import TextParser
from app.rag.types import ParsedDocument

_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UnsupportedContentType(Exception):
    """Raised when no parser handles the given content type."""


class ParserDispatcher:
    """Selects the right parser adapter by (already-sniffed) content type."""

    def __init__(self, ocr: OcrProvider, ocr_enabled: bool, min_chars: int) -> None:
        self._text = TextParser()
        self._pptx = PptxParser()
        self._docx = DocxParser()
        self._image = ImageParser(ocr)
        self._pdf = PdfParser(ocr=ocr, ocr_enabled=ocr_enabled, min_chars=min_chars)

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        if content_type == "application/pdf":
            return self._pdf.parse(data, content_type)
        if content_type == _PPTX:
            return self._pptx.parse(data, content_type)
        if content_type == _DOCX:
            return self._docx.parse(data, content_type)
        if content_type in ("text/plain", "text/markdown"):
            return self._text.parse(data, content_type)
        if content_type in ("image/png", "image/jpeg"):
            return self._image.parse(data, content_type)
        raise UnsupportedContentType(content_type)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_parsing.py -v`
Expected: PASS. *(The image-only-PDF fallback test requires poppler installed locally; if poppler is missing it errors — install poppler, or temporarily `-k "not image_only"` while developing, but it MUST pass in CI where poppler is installed.)*

- [ ] **Step 5: Commit**
```bash
git add backend/app/rag/parsing backend/tests/test_parsing.py
git commit -m "feat: DocumentParser adapters (text/office/image/pdf) + dispatcher"
```

> **MILESTONE B BOUNDARY — STOP. Ask the user to review + commit before continuing.**

---

## Milestone C — Repositories

### Task 13: DocumentRepository

**Files:** Create `app/db/repositories/document.py`; Test `tests/test_document_repository.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_document_repository.py`:
```python
import uuid

import pytest

from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.models.user import User


async def _user(db_session) -> User:
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


def _doc_kwargs(user_id: uuid.UUID, **over: object) -> dict:
    base = dict(
        user_id=user_id,
        filename="a.pdf",
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/a",
        file_size=1,
        chunk_count=0,
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_create_and_list_for_user(db_session):
    user = await _user(db_session)
    repo = DocumentRepository(db_session)
    await repo.create(**_doc_kwargs(user.id, filename="a.pdf"))
    await repo.create(**_doc_kwargs(user.id, filename="b.pdf", course="BIO"))
    await db_session.commit()

    docs = await repo.list_for_user(user.id)
    assert {d.filename for d in docs} == {"a.pdf", "b.pdf"}

    bio = await repo.list_for_user(user.id, course="BIO")
    assert [d.filename for d in bio] == ["b.pdf"]


@pytest.mark.asyncio
async def test_get_by_user_and_hash(db_session):
    user = await _user(db_session)
    repo = DocumentRepository(db_session)
    h = uuid.uuid4().hex
    await repo.create(**_doc_kwargs(user.id, content_hash=h))
    await db_session.commit()
    assert await repo.get_by_user_and_hash(user.id, h) is not None
    assert await repo.get_by_user_and_hash(user.id, "nope") is None


@pytest.mark.asyncio
async def test_get_for_user_enforces_ownership(db_session):
    a = await _user(db_session)
    b = await _user(db_session)
    repo = DocumentRepository(db_session)
    doc = await repo.create(**_doc_kwargs(a.id))
    await db_session.commit()
    assert await repo.get_for_user(doc.id, a.id) is not None
    assert await repo.get_for_user(doc.id, b.id) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_document_repository.py -v`
Expected: FAIL (`ModuleNotFoundError: app.db.repositories.document`).

- [ ] **Step 3: Implement**

Create `app/db/repositories/document.py`:
```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.document import Document


class DocumentRepository(BaseRepository[Document]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Document, session)

    async def list_for_user(
        self, user_id: uuid.UUID, course: str | None = None
    ) -> list[Document]:
        stmt = select(Document).where(Document.user_id == user_id)
        if course is not None:
            stmt = stmt.where(Document.course == course)
        stmt = stmt.order_by(Document.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> Document | None:
        stmt = select(Document).where(
            Document.id == document_id, Document.user_id == user_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_and_hash(
        self, user_id: uuid.UUID, content_hash: str
    ) -> Document | None:
        stmt = select(Document).where(
            Document.user_id == user_id, Document.content_hash == content_hash
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_document_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/db/repositories/document.py backend/tests/test_document_repository.py
git commit -m "feat: DocumentRepository (list/get/dedup, per-user)"
```

---

### Task 14: ChunkRepository (bulk insert + pgvector search)

**Files:** Create `app/db/repositories/chunk.py`; Test `tests/test_chunk_repository.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chunk_repository.py`:
```python
import uuid

import pytest

from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository

DIM = 1536


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot] = 1.0
    return v


async def _user_and_doc(db_session, course=None):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(
        user_id=user.id,
        filename="a.pdf",
        title="Lecture A",
        course=course,
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/a",
        file_size=1,
        chunk_count=0,
        embedding_model="gemini-embedding-001",
        embedding_dimension=DIM,
    )
    await db_session.commit()
    return user, doc


@pytest.mark.asyncio
async def test_add_many_and_search_orders_by_similarity(db_session):
    user, doc = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [
            dict(document_id=doc.id, user_id=user.id, chunk_index=0,
                 content="far", embedding=_vec(5)),
            dict(document_id=doc.id, user_id=user.id, chunk_index=1,
                 content="near", embedding=_vec(0)),
        ]
    )
    await db_session.commit()

    results = await repo.search_similar(user.id, _vec(0), top_k=2)
    assert [r.content for r in results] == ["near", "far"]
    assert results[0].filename == "a.pdf"
    assert results[0].title == "Lecture A"
    assert results[0].score >= results[1].score


@pytest.mark.asyncio
async def test_search_is_scoped_to_user(db_session):
    user_a, doc_a = await _user_and_doc(db_session)
    user_b, doc_b = await _user_and_doc(db_session)
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [dict(document_id=doc_b.id, user_id=user_b.id, chunk_index=0,
              content="b-only", embedding=_vec(0))]
    )
    await db_session.commit()
    results = await repo.search_similar(user_a.id, _vec(0), top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_search_filters_by_course(db_session):
    user, doc = await _user_and_doc(db_session, course="BIO")
    repo = ChunkRepository(db_session)
    await repo.add_many(
        [dict(document_id=doc.id, user_id=user.id, chunk_index=0,
              content="bio chunk", embedding=_vec(0))]
    )
    await db_session.commit()
    assert len(await repo.search_similar(user.id, _vec(0), top_k=5, course="BIO")) == 1
    assert await repo.search_similar(user.id, _vec(0), top_k=5, course="MATH") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_chunk_repository.py -v`
Expected: FAIL (`ModuleNotFoundError: app.db.repositories.chunk`).

- [ ] **Step 3: Implement**

Create `app/db/repositories/chunk.py`:
```python
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.document import Document, DocumentChunk


@dataclass(frozen=True)
class ChunkSearchResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    title: str | None
    content: str
    page_number: int | None
    section: str | None
    score: float  # cosine similarity in [0, 1]; higher is closer


class ChunkRepository(BaseRepository[DocumentChunk]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DocumentChunk, session)

    async def add_many(self, rows: list[dict]) -> None:
        self._session.add_all([DocumentChunk(**row) for row in rows])
        await self._session.flush()

    async def search_similar(
        self,
        user_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int,
        course: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ChunkSearchResult]:
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                Document.filename,
                Document.title,
                DocumentChunk.content,
                DocumentChunk.page_number,
                DocumentChunk.section,
                distance.label("distance"),
            )
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.user_id == user_id)
        )
        if course is not None:
            stmt = stmt.where(Document.course == course)
        if tags:
            stmt = stmt.where(Document.tags.contains(tags))
        stmt = stmt.order_by("distance").limit(top_k)

        result = await self._session.execute(stmt)
        return [
            ChunkSearchResult(
                chunk_id=row.id,
                document_id=row.document_id,
                filename=row.filename,
                title=row.title,
                content=row.content,
                page_number=row.page_number,
                section=row.section,
                score=1.0 - float(row.distance),
            )
            for row in result.all()
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_chunk_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/db/repositories/chunk.py backend/tests/test_chunk_repository.py
git commit -m "feat: ChunkRepository (bulk insert + pgvector cosine search, per-user)"
```

> **MILESTONE C BOUNDARY — STOP. Ask the user to review + commit before continuing.**

---

## Milestone D — Services

### Task 15: IngestionService

**Files:** Create `app/services/ingestion.py`; Test `tests/test_ingestion_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ingestion_service.py`:
```python
import hashlib
import uuid
from pathlib import Path

import pytest

from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.rag.chunking import Chunker
from app.rag.storage import LocalFileStorage
from app.services.ingestion import DuplicateDocument, IngestionService
from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider


async def _user(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


def _service(db_session, storage, parser=None):
    from app.rag.parsing import ParserDispatcher

    return IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=parser or ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=30, chunk_overlap_tokens=5),
        embeddings=FakeEmbeddingsProvider(),
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )


@pytest.mark.asyncio
async def test_ingest_persists_document_and_chunks(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)

    data = b"first paragraph of notes. second paragraph of notes."
    doc = await svc.ingest(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=data, title="My Notes", course="BIO", tags=["midterm"],
    )

    assert doc.title == "My Notes"
    assert doc.course == "BIO"
    assert doc.tags == ["midterm"]
    assert doc.chunk_count >= 1
    assert doc.content_hash == hashlib.sha256(data).hexdigest()
    assert Path(doc.storage_path).exists()

    chunks = await ChunkRepository(db_session).list()
    assert len(chunks) == doc.chunk_count
    assert all(len(c.embedding) == 1536 for c in chunks)


@pytest.mark.asyncio
async def test_duplicate_upload_raises_and_writes_no_new_rows(db_session, tmp_path):
    user = await _user(db_session)
    svc = _service(db_session, LocalFileStorage(str(tmp_path)))
    data = b"same content here"
    await svc.ingest(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)

    before = len(await DocumentRepository(db_session).list_for_user(user.id))
    with pytest.raises(DuplicateDocument):
        await svc.ingest(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)
    after = len(await DocumentRepository(db_session).list_for_user(user.id))
    assert before == after == 1


@pytest.mark.asyncio
async def test_failure_rolls_back_and_deletes_file(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    from app.rag.parsing import ParserDispatcher

    svc = IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=30, chunk_overlap_tokens=5),
        embeddings=BoomEmbeddings(),
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )

    with pytest.raises(RuntimeError):
        await svc.ingest(
            user_id=user.id, filename="x.txt", content_type="text/plain", data=b"some text here"
        )

    assert await DocumentRepository(db_session).list_for_user(user.id) == []
    # No leftover files anywhere under the storage root.
    assert not any(Path(tmp_path).rglob("*.*"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ingestion_service.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.ingestion`).

- [ ] **Step 3: Implement**

Create `app/services/ingestion.py`:
```python
import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.models.document import Document
from app.rag.chunking import Chunker
from app.rag.embeddings import EmbeddingsProvider
from app.rag.parsing import ParserDispatcher
from app.rag.storage import StorageBackend


class IngestionError(Exception):
    """Base class for ingestion domain errors."""


class DuplicateDocument(IngestionError):
    """Raised when the same file (by content hash) is already ingested for the user."""

    def __init__(self, existing: Document) -> None:
        self.existing = existing
        super().__init__(str(existing.id))


class IngestionService:
    """Atomic ingestion: store file → parse → chunk → embed → persist. On any failure,
    rolls back the DB transaction AND deletes the stored file (compensating cleanup)."""

    def __init__(
        self,
        session: AsyncSession,
        documents: DocumentRepository,
        chunks: ChunkRepository,
        storage: StorageBackend,
        parser: ParserDispatcher,
        chunker: Chunker,
        embeddings: EmbeddingsProvider,
        embedding_model: str,
        embedding_dimension: int,
    ) -> None:
        self._session = session
        self._documents = documents
        self._chunks = chunks
        self._storage = storage
        self._parser = parser
        self._chunker = chunker
        self._embeddings = embeddings
        self._embedding_model = embedding_model
        self._embedding_dimension = embedding_dimension

    async def ingest(
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
        content_hash = hashlib.sha256(data).hexdigest()
        existing = await self._documents.get_by_user_and_hash(user_id, content_hash)
        if existing is not None:
            raise DuplicateDocument(existing)

        storage_path = self._storage.save(user_id, filename, data)
        try:
            parsed = self._parser.parse(data, content_type)
            chunks = self._chunker.split(parsed)
            vectors = self._embeddings.embed_documents([c.content for c in chunks])

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
                page_count=parsed.page_count,
                chunk_count=len(chunks),
                embedding_model=self._embedding_model,
                embedding_dimension=self._embedding_dimension,
            )
            await self._chunks.add_many(
                [
                    dict(
                        document_id=document.id,
                        user_id=user_id,
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
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            self._storage.delete(storage_path)
            raise
        return document
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_ingestion_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/ingestion.py backend/tests/test_ingestion_service.py
git commit -m "feat: IngestionService (atomic pipeline + dedup + cleanup)"
```

---

### Task 16: RetrievalService

**Files:** Create `app/services/retrieval.py`; Test `tests/test_retrieval_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_retrieval_service.py`:
```python
import uuid

import pytest

from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.services.retrieval import RetrievalService
from tests.fakes import FakeEmbeddingsProvider

DIM = 1536


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot] = 1.0
    return v


@pytest.mark.asyncio
async def test_search_embeds_query_and_returns_matches(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    doc = await DocumentRepository(db_session).create(
        user_id=user.id, filename="a.pdf", content_type="application/pdf",
        content_hash=uuid.uuid4().hex, storage_path="/tmp/a", file_size=1, chunk_count=1,
        embedding_model="m", embedding_dimension=DIM,
    )
    # FakeEmbeddingsProvider maps text -> one-hot at (len(text) % dim).
    await ChunkRepository(db_session).add_many(
        [dict(document_id=doc.id, user_id=user.id, chunk_index=0,
              content="abc", embedding=_vec(len("query!") % DIM))]
    )
    await db_session.commit()

    svc = RetrievalService(ChunkRepository(db_session), FakeEmbeddingsProvider(), default_top_k=5)
    results = await svc.search(user.id, "query!")
    assert len(results) == 1
    assert results[0].content == "abc"


@pytest.mark.asyncio
async def test_empty_query_returns_no_results(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    svc = RetrievalService(ChunkRepository(db_session), FakeEmbeddingsProvider(), default_top_k=5)
    assert await svc.search(user.id, "   ") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_retrieval_service.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.retrieval`).

- [ ] **Step 3: Implement**

Create `app/services/retrieval.py`:
```python
import uuid

from app.db.repositories.chunk import ChunkRepository, ChunkSearchResult
from app.rag.embeddings import EmbeddingsProvider


class RetrievalService:
    """Embeds a query and returns the user's most similar chunks."""

    def __init__(
        self,
        chunks: ChunkRepository,
        embeddings: EmbeddingsProvider,
        default_top_k: int,
    ) -> None:
        self._chunks = chunks
        self._embeddings = embeddings
        self._default_top_k = default_top_k

    async def search(
        self,
        user_id: uuid.UUID,
        query: str,
        top_k: int | None = None,
        course: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ChunkSearchResult]:
        if not query.strip():
            return []
        embedding = self._embeddings.embed_query(query)
        return await self._chunks.search_similar(
            user_id,
            embedding,
            top_k=top_k or self._default_top_k,
            course=course,
            tags=tags,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_retrieval_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/retrieval.py backend/tests/test_retrieval_service.py
git commit -m "feat: RetrievalService (query embed + similarity search)"
```

> **MILESTONE D BOUNDARY — STOP. Ask the user to review + commit before continuing.**

---

## Milestone E — Schemas, dependencies, API

### Task 17: Schemas

**Files:** Create `app/schemas/document.py`; Test `tests/test_document_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_document_schemas.py`:
```python
import pytest
from pydantic import ValidationError

from app.schemas.document import SearchRequest


def test_search_request_defaults_and_bounds():
    assert SearchRequest(query="hi").top_k == 5
    with pytest.raises(ValidationError):
        SearchRequest(query="hi", top_k=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="", top_k=3)
    with pytest.raises(ValidationError):
        SearchRequest(query="hi", top_k=999)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_document_schemas.py -v`
Expected: FAIL (`ModuleNotFoundError: app.schemas.document`).

- [ ] **Step 3: Implement**

Create `app/schemas/document.py`:
```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    title: str | None
    course: str | None
    tags: list[str]
    content_type: str
    page_count: int | None
    chunk_count: int
    file_size: int
    embedding_model: str
    embedding_dimension: int
    created_at: datetime
    updated_at: datetime


class DuplicateDocumentResponse(BaseModel):
    detail: str = "Document already exists"
    document_id: UUID


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    course: str | None = None
    tags: list[str] | None = None


class ChunkMatch(BaseModel):
    chunk_id: UUID
    document_id: UUID
    filename: str
    title: str | None
    content: str
    page_number: int | None
    section: str | None
    score: float
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_document_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/schemas/document.py backend/tests/test_document_schemas.py
git commit -m "feat: document + search Pydantic schemas"
```

---

### Task 18: Dependency wiring

**Files:** Modify `app/api/deps.py`

- [ ] **Step 1: Add provider + service dependencies**

Append the new functions to `app/api/deps.py`. **Merge** these imports into the file's existing import block — do NOT re-import names already present (`Depends` is already imported from `fastapi`; `AsyncSession` from `sqlalchemy.ext.asyncio`; `get_db` from `app.db.session`). Add only what's missing:
```python
# add to existing `from app.core.config import ...` (currently unused there) :
from app.core.config import Settings, get_settings
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.rag.chunking import Chunker
from app.rag.embeddings import EmbeddingsProvider, GeminiEmbeddingsProvider
from app.rag.ocr import OcrProvider, TesseractOcr
from app.rag.parsing import ParserDispatcher
from app.rag.storage import LocalFileStorage, StorageBackend
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService


def get_storage(settings: Settings = Depends(get_settings)) -> StorageBackend:  # noqa: B008
    return LocalFileStorage(settings.upload_dir)


def get_ocr(settings: Settings = Depends(get_settings)) -> OcrProvider:  # noqa: B008
    return TesseractOcr(language=settings.ocr_language, cmd=settings.tesseract_cmd)


def get_embeddings(settings: Settings = Depends(get_settings)) -> EmbeddingsProvider:  # noqa: B008
    return GeminiEmbeddingsProvider(settings)


def get_parser(
    settings: Settings = Depends(get_settings),  # noqa: B008
    ocr: OcrProvider = Depends(get_ocr),  # noqa: B008
) -> ParserDispatcher:
    return ParserDispatcher(
        ocr=ocr,
        ocr_enabled=settings.ocr_enabled,
        min_chars=settings.pdf_ocr_min_chars_per_page,
    )


def get_chunker(settings: Settings = Depends(get_settings)) -> Chunker:  # noqa: B008
    return Chunker(
        chunk_tokens=settings.chunk_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
    )


def get_ingestion_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    storage: StorageBackend = Depends(get_storage),  # noqa: B008
    parser: ParserDispatcher = Depends(get_parser),  # noqa: B008
    chunker: Chunker = Depends(get_chunker),  # noqa: B008
    embeddings: EmbeddingsProvider = Depends(get_embeddings),  # noqa: B008
) -> IngestionService:
    return IngestionService(
        session=session,
        documents=DocumentRepository(session),
        chunks=ChunkRepository(session),
        storage=storage,
        parser=parser,
        chunker=chunker,
        embeddings=embeddings,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
    )


def get_retrieval_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    embeddings: EmbeddingsProvider = Depends(get_embeddings),  # noqa: B008
) -> RetrievalService:
    return RetrievalService(
        chunks=ChunkRepository(session),
        embeddings=embeddings,
        default_top_k=settings.retrieval_top_k,
    )
```

- [ ] **Step 2: Verify imports resolve**

Run: `cd backend && uv run python -c "import app.api.deps; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**
```bash
git add backend/app/api/deps.py
git commit -m "feat: DI for storage/ocr/embeddings/parser/chunker + ingestion/retrieval services"
```

---

### Task 19: Test fixtures — fake-provider overrides + authed client

**Files:** Modify `tests/conftest.py`

- [ ] **Step 1: Extend the `client` fixture to inject fakes + temp storage, and add `auth_client`**

In `tests/conftest.py`, replace the `client` fixture with the version below and add the `auth_client` fixture after it:
```python
@pytest_asyncio.fixture
async def client(_engine: AsyncEngine, tmp_path) -> AsyncGenerator[AsyncClient]:
    """HTTP client whose app talks to the TEST DB and uses fake OCR/embeddings + temp storage."""
    from app.api.deps import get_embeddings, get_ocr, get_storage
    from app.db.session import get_db
    from app.main import create_app
    from app.rag.storage import LocalFileStorage
    from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider

    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_embeddings] = lambda: FakeEmbeddingsProvider()
    app.dependency_overrides[get_ocr] = lambda: FakeOcrProvider()
    app.dependency_overrides[get_storage] = lambda: LocalFileStorage(str(tmp_path))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
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
```

- [ ] **Step 2: Verify the existing suite still passes**

Run: `cd backend && uv run pytest -q`
Expected: all prior tests still PASS (fixture changes are additive/compatible).

- [ ] **Step 3: Commit**
```bash
git add backend/tests/conftest.py
git commit -m "test: inject fake providers + temp storage; add auth_client fixture"
```

---

### Task 20: Documents router (upload / list / delete)

**Files:** Create `app/api/documents.py`; Modify `app/main.py`; Test `tests/test_documents_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_documents_api.py`:
```python
import pytest


@pytest.mark.asyncio
async def test_upload_list_delete_flow(auth_client):
    files = {"file": ("notes.txt", b"alpha beta gamma. delta epsilon.", "text/plain")}
    resp = await auth_client.post(
        "/documents", files=files, data={"title": "My Notes", "course": "BIO"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "My Notes"
    assert body["chunk_count"] >= 1
    doc_id = body["id"]

    listed = await auth_client.get("/documents")
    assert listed.status_code == 200
    assert any(d["id"] == doc_id for d in listed.json())

    filtered = await auth_client.get("/documents", params={"course": "BIO"})
    assert [d["id"] for d in filtered.json()] == [doc_id]

    deleted = await auth_client.delete(f"/documents/{doc_id}")
    assert deleted.status_code == 204
    after = await auth_client.get("/documents")
    assert all(d["id"] != doc_id for d in after.json())


@pytest.mark.asyncio
async def test_upload_requires_auth(client):
    files = {"file": ("notes.txt", b"hi", "text/plain")}
    resp = await client.post("/documents", files=files)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unsupported_type_rejected(auth_client):
    files = {"file": ("evil.exe", b"MZ\x90\x00bad", "application/octet-stream")}
    resp = await auth_client.post("/documents", files=files)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_upload_returns_409(auth_client):
    data = b"identical content for dedup"
    files = {"file": ("a.txt", data, "text/plain")}
    first = await auth_client.post("/documents", files=files)
    assert first.status_code == 201
    again = await auth_client.post(
        "/documents", files={"file": ("a.txt", data, "text/plain")}
    )
    assert again.status_code == 409
    assert again.json()["document_id"] == first.json()["id"]


@pytest.mark.asyncio
async def test_delete_not_owned_returns_404(auth_client, client):
    # auth_client uploads; a second fresh user (via client+own login) can't delete it.
    files = {"file": ("a.txt", b"owner content", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=files)).json()["id"]

    import uuid as _uuid

    email = f"other-{_uuid.uuid4().hex}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    token = (await client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    )).json()["access_token"]
    resp = await client.delete(
        f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_documents_api.py -v`
Expected: FAIL (404 routes — router not registered yet).

- [ ] **Step 3: Implement the router**

Create `app/api/documents.py`:
```python
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import get_current_user, get_ingestion_service
from app.core.config import get_settings
from app.db.repositories.document import DocumentRepository
from app.db.session import get_db
from app.models.user import User
from app.rag.storage import LocalFileStorage
from app.schemas.document import DocumentResponse, DuplicateDocumentResponse
from app.services.ingestion import DuplicateDocument, IngestionService
from app.utils.files import sanitize_filename, sniff_content_type

router = APIRouter(prefix="/documents", tags=["documents"])


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
        document = await service.ingest(
            user_id=current_user.id,
            filename=sanitize_filename(file.filename or "upload"),
            content_type=content_type,
            data=data,
            title=title,
            course=course,
            tags=tags,
        )
    except DuplicateDocument as exc:
        # Return a JSONResponse (not HTTPException) so document_id is top-level,
        # matching DuplicateDocumentResponse rather than being nested under "detail".
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Document already exists", "document_id": str(exc.existing.id)},
        )
    return DocumentResponse.model_validate(document)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    course: str | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    session=Depends(get_db),  # noqa: B008
) -> list[DocumentResponse]:
    docs = await DocumentRepository(session).list_for_user(current_user.id, course=course)
    return [DocumentResponse.model_validate(d) for d in docs]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    session=Depends(get_db),  # noqa: B008
) -> Response:
    repo = DocumentRepository(session)
    doc = await repo.get_for_user(document_id, current_user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    storage_path = doc.storage_path
    await repo.delete(doc)  # cascade removes chunks
    await session.commit()
    LocalFileStorage(get_settings().upload_dir).delete(storage_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

> **Storage-on-delete note:** the test `client` overrides `get_storage` to a temp dir, but `delete_document` constructs `LocalFileStorage(upload_dir)` directly. Since the stored path is absolute (from the temp-dir storage used at upload), deleting via a default-root `LocalFileStorage` still works because `delete()` takes the absolute path. This is fine for tests; prod behavior is correct because uploads also store absolute paths.

- [ ] **Step 4: Register the router in `app/main.py`**

In `app/main.py`, change the import line:
```python
from app.api import auth, health
```
to:
```python
from app.api import auth, documents, health, search
```
and add after `app.include_router(auth.router)`:
```python
    app.include_router(documents.router)
    app.include_router(search.router)
```
(`search` is created in Task 21; if executing strictly in order, temporarily register only `documents` now and add `search` in Task 21. Recommended: do Task 21 before running main.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_documents_api.py -v`
Expected: PASS (after Task 21 exists, or with `search` import temporarily removed).

- [ ] **Step 6: Commit**
```bash
git add backend/app/api/documents.py backend/app/main.py backend/tests/test_documents_api.py
git commit -m "feat: documents API (upload/list/delete) with dedup + ownership"
```

---

### Task 21: Search router

**Files:** Create `app/api/search.py`; Test `tests/test_search_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_search_api.py`:
```python
import pytest


@pytest.mark.asyncio
async def test_search_returns_user_chunks(auth_client):
    files = {"file": ("notes.txt", b"mitochondria is the powerhouse of the cell.", "text/plain")}
    assert (await auth_client.post("/documents", files=files)).status_code == 201

    resp = await auth_client.post("/search", json={"query": "what is the powerhouse", "top_k": 3})
    assert resp.status_code == 200, resp.text
    matches = resp.json()
    assert len(matches) >= 1
    assert "filename" in matches[0] and "score" in matches[0]


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    resp = await client.post("/search", json={"query": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_only_sees_own_documents(auth_client, client):
    # auth_client uploads a doc; a different user's search returns nothing.
    await auth_client.post(
        "/documents", files={"file": ("a.txt", b"private notes content", "text/plain")}
    )
    import uuid as _uuid

    email = f"other-{_uuid.uuid4().hex}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    token = (await client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    )).json()["access_token"]
    resp = await client.post(
        "/search", json={"query": "private notes"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_search_api.py -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Implement**

Create `app/api/search.py`:
```python
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, get_retrieval_service
from app.models.user import User
from app.schemas.document import ChunkMatch, SearchRequest
from app.services.retrieval import RetrievalService

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=list[ChunkMatch])
async def search(
    body: SearchRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: RetrievalService = Depends(get_retrieval_service),  # noqa: B008
) -> list[ChunkMatch]:
    results = await service.search(
        current_user.id,
        body.query,
        top_k=body.top_k,
        course=body.course,
        tags=body.tags,
    )
    return [
        ChunkMatch(
            chunk_id=r.chunk_id,
            document_id=r.document_id,
            filename=r.filename,
            title=r.title,
            content=r.content,
            page_number=r.page_number,
            section=r.section,
            score=r.score,
        )
        for r in results
    ]
```
Ensure `search.router` is registered in `app/main.py` (Task 20 Step 4).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_search_api.py tests/test_documents_api.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + lint + types**

Run:
```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy app
```
Expected: all green. Fix any ruff/mypy issues inline (e.g. add `# noqa: B008` on `Depends(...)` defaults to match the existing codebase convention).

- [ ] **Step 6: Commit**
```bash
git add backend/app/api/search.py
git commit -m "feat: search API (per-user semantic retrieval)"
```

> **MILESTONE E BOUNDARY — STOP. Ask the user to review + commit before continuing.**

---

## Milestone F — Ops & docs

### Task 22: Docker + compose (tesseract/poppler + uploads volume)

**Files:** Modify `backend/Dockerfile`, `docker-compose.yml`

- [ ] **Step 1: Install system deps in the image**

In `backend/Dockerfile`, after the `WORKDIR /app` line (before copying deps), add:
```dockerfile
# OCR system dependencies: tesseract (OCR engine) + poppler (pdf2image rasterization)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Add the uploads volume + env in compose**

In `docker-compose.yml`, under the `backend` service `environment:` block add:
```yaml
      UPLOAD_DIR: /data/uploads
      GOOGLE_API_KEY: ${GOOGLE_API_KEY:-}
```
Add a `volumes:` block to the `backend` service:
```yaml
    volumes:
      - uploads_data:/data/uploads
```
And add `uploads_data:` under the top-level `volumes:` key (alongside `postgres_data:`):
```yaml
volumes:
  postgres_data:
  uploads_data:
```

- [ ] **Step 3: Build + smoke test**

Run: `docker compose build backend`
Expected: build succeeds; `tesseract` + `poppler` install without error.

Optionally: `docker compose run --rm backend sh -c "tesseract --version && pdftoppm -v"`
Expected: both binaries report versions.

- [ ] **Step 4: Commit**
```bash
git add backend/Dockerfile docker-compose.yml
git commit -m "build: install tesseract+poppler; mount uploads volume"
```

---

### Task 23: CI — install OCR system deps

**Files:** Modify `.github/workflows/ci.yml`

- [ ] **Step 1: Add a system-deps install step before tests**

In `.github/workflows/ci.yml`, in the job that runs pytest, add a step BEFORE the test step (matching the existing runner syntax):
```yaml
      - name: Install OCR system dependencies
        run: sudo apt-get update && sudo apt-get install -y tesseract-ocr poppler-utils
```
(If CI provisions the test Postgres via a service container with `TEST_DATABASE_URL`, leave that as-is — unchanged by this task.)

- [ ] **Step 2: Verify locally that the test command used by CI passes**

Run: `cd backend && uv run pytest -q`
Expected: PASS (the real-Tesseract test runs if tesseract is present, else self-skips).

- [ ] **Step 3: Commit**
```bash
git add .github/workflows/ci.yml
git commit -m "ci: install tesseract + poppler for OCR tests"
```

---

### Task 24: Learning doc

**Files:** Create `docs/learning/02-ingestion-retrieval.md`

- [ ] **Step 1: Write the explainer**

Create `docs/learning/02-ingestion-retrieval.md` covering, in plain English (mirroring the style of `docs/learning/01-auth.md`):
- The ingestion assembly line (store → parse → chunk → embed → persist) and why it's atomic-with-cleanup.
- Ports & adapters here: `StorageBackend`, `DocumentParser` (+ per-format adapters), `OcrProvider`, `EmbeddingsProvider` — and why the vector store is just a repository.
- Embeddings: what an embedding is, why Gemini `gemini-embedding-001` at 1536 dims, L2-normalization, and asymmetric `RETRIEVAL_DOCUMENT`/`RETRIEVAL_QUERY` task types.
- Chunking: token-based recursive splitting within structural boundaries (slides/headings/pages) and why structure-aware beats blind fixed-size; mention deferred semantic / small-to-big / contextual retrieval (Phase 3).
- pgvector: the `Vector(1536)` column, cosine distance (`<=>`), HNSW index, and the per-user `WHERE` filter.
- OCR: Tesseract, the per-page PDF fallback, and the tesseract/poppler system requirement.
- Dedup via content hash; per-user isolation; why `/search` exists with no LLM yet (Phase 3 hook).

- [ ] **Step 2: Commit**
```bash
git add docs/learning/02-ingestion-retrieval.md
git commit -m "docs: Phase 2 ingestion + retrieval learning guide"
```

---

### Task 25: Final verification + manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Full automated gate**

Run:
```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy app
```
Expected: all PASS / clean.

- [ ] **Step 2: Live smoke test (requires a real `GOOGLE_API_KEY` in `backend/.env` + tesseract/poppler installed)**

Run `make dev`, then at `http://localhost:8000/docs`:
1. Register + login (Authorize with the access token).
2. `POST /documents` with a small PDF and a TXT (set `title`/`course`).
3. `GET /documents` → both listed; `?course=` filters.
4. `POST /search` with a phrase from the notes → relevant chunks ranked, with `filename`/`section`/`score`.
5. Re-upload the same file → `409`.
6. `DELETE /documents/{id}` → `204`; confirm it's gone from list and the file is removed from the uploads dir.
7. (Optional) Upload an image / scanned PDF → OCR text becomes searchable.

- [ ] **Step 3: Self-review against the spec** — confirm every spec section (formats, dedup, atomic cleanup, structure-aware chunking, per-user isolation, OCR fallback) maps to a passing test or a verified smoke step. Note any gaps.

> **MILESTONE F BOUNDARY — STOP. Hand back to the user for final review + commit. Then: push, confirm CI is green, and consider opening a PR `development → main`.**

---

## Definition of Done (from the spec)

- [ ] Upload works end-to-end for PDF, PPTX, DOCX, TXT, MD, PNG, JPG (+ scanned PDFs via OCR).
- [ ] `GET /documents` (with `?course=`), `DELETE /documents/{id}`, `POST /search` work, all per-user scoped.
- [ ] Re-uploading an identical file → `409`, no duplicate embedding.
- [ ] Ingestion is atomic: failure rolls back the DB and removes the stored file.
- [ ] All tests pass against `notes_rag_test`; `ruff` + `mypy` clean.
- [ ] `docs/learning/02-ingestion-retrieval.md` written.
- [ ] CI installs tesseract+poppler and is green after push.
