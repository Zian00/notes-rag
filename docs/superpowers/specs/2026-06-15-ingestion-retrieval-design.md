# Phase 2 — Ingestion + Retrieval: Design Spec

**Date:** 2026-06-15
**Status:** Approved (pending written-spec review)
**Phase:** 2 of 5 (see roadmap in `docs/superpowers/specs/2026-06-13-foundation-design.md`)

---

## 1. Context

Phases 0 (Foundation) and 1 (Auth) are complete: a FastAPI backend talks to Postgres+pgvector,
with per-user JWT auth and the layered + hexagonal architecture established. Phase 2 delivers the
**data plane** of the RAG system: turning an uploaded file into searchable, per-user vector data,
plus a plain retrieval endpoint to verify it.

**There is no LLM and no agent in this phase.** Phase 2 stops at "embed the query, return the
nearest chunks." Phase 3 (LangGraph agentic RAG) sits on top of this retrieval to actually answer
questions and self-correct. The design is deliberately reframed around **production RAG ingestion
practice**, not the legacy Streamlit app — several legacy choices (character-based chunking,
regex-from-filename metadata, a now-deprecated embedding model) are explicitly replaced.

### Phase 2 goal

A user can upload lecture notes in the common formats — **PDF, PowerPoint (PPTX), Word (DOCX),
TXT, MD, and images (PNG/JPG, including scanned/image-only PDFs via OCR)** — have them parsed →
chunked → embedded → stored in pgvector scoped to their account, manage those documents
(list/delete), and run a plain semantic search that returns the most relevant chunks. Running,
tested, and documented.

### Definition of done

- `POST /documents` ingests a file end-to-end (parse → chunk → embed → store) for every supported
  format and returns the created document; `GET /documents`, `DELETE /documents/{id}`, and
  `POST /search` work, all per-user scoped.
- Re-uploading an identical file is idempotent (`409 Conflict`, no duplicate embedding).
- Ingestion is atomic: any failure rolls back the DB **and** removes the stored file.
- All tests pass against the dedicated `notes_rag_test` DB; `ruff` and `mypy` clean.
- `docs/learning/02-ingestion-retrieval.md` explains the new concepts.

---

## 2. Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Embedding model | **`gemini-embedding-001`** | Current GA model. `text-embedding-004` was **deprecated 2026-01-14**. |
| Embedding dimension | **1536** (Matryoshka), L2-normalized | Fits pgvector `vector` + HNSW (≤2000-dim cap); good quality/size balance. Truncated dims aren't auto-normalized, so the adapter normalizes. |
| Task types | `RETRIEVAL_DOCUMENT` for chunks, `RETRIEVAL_QUERY` for queries | Asymmetric embedding improves retrieval quality. |
| Embedding SDK | Official `google-genai` (behind an ABC) | No full-LangChain dependency for embeddings; isolated behind `EmbeddingsProvider`. |
| Chunking | **Structure-aware recursive token** — recursive token splitting (≈512 tokens / ≈64 overlap, tiktoken encoder) applied *within* natural boundaries: one segment per PPTX slide, Markdown split on headings, never split across PDF pages; heading/slide stored as chunk metadata | Chunk size belongs in the model's units; respecting boundaries the parser already knows keeps chunks coherent for structured lecture notes. Semantic/LLM-based chunking and small-to-big/contextual retrieval are deferred (Phase 3). |
| Supported formats | **PDF, PPTX, DOCX, TXT, MD, PNG, JPG** (+ scanned/image-only PDFs) | Realistic formats for lecture material; everything else → `400`. |
| Parsing | Per-format adapters behind `DocumentParser`: `pypdf` (PDF), `python-pptx`, `python-docx`, utf-8 (TXT/MD), Tesseract (images) | Lightweight, Windows-friendly, explicit; the port lets us add formats without touching the pipeline. |
| OCR | **Tesseract** (`pytesseract`) behind an `OcrProvider` port; PDF rasterization via `pdf2image` (poppler) | Local, free, no API cost. Engine is swappable (e.g. cloud OCR) via the port. |
| PDF OCR fallback | **Per-page**: pages with no extractable text are rasterized and OCR'd | Handles scanned and mixed text/scanned PDFs correctly. |
| Processing model | **Synchronous + atomic-with-cleanup** | Simplest correct slice for a single-user app. NB: OCR is slow → **background processing is a strong fast-follow** (deferred to a later phase). |
| File storage | Local filesystem volume behind `StorageBackend` | Path in DB; swappable to object storage later with no app changes. |
| Deduplication | `sha256` content hash, unique per `(user_id, content_hash)` | Idempotent ingestion; avoids re-embedding identical files. |
| Metadata | User-supplied at upload (`title`/`course`/`tags`), **nullable**; Phase 3 LLM backfills blanks | Explicit at ingestion (industrial), with a forward hook for auto-enrichment. |
| Vector access | `ChunkRepository` (CRUD + similarity query) — **no separate `VectorStore` ABC** | Committed to pgvector; a port over one concrete store is YAGNI. The swappable port is `EmbeddingsProvider`. |

**Replaced legacy choices:** character-based 800/120 chunking → token-based 512/64; regex
`lecture_id` from filename → explicit user metadata; `text-embedding-004` (768) → `gemini-embedding-001`
(1536); ChromaDB → pgvector; trust client `Content-Type` → content sniffing.

---

## 3. Architecture

Follows the established layered + hexagonal style (see Phase 0 spec §3a/§3b).

```
POST /documents (multipart)                         ── Boundary (api/documents.py)
        │  validate type+size, parse form fields
        ▼
IngestionService.ingest(user_id, file, metadata)    ── Control (services/ingestion.py)
   │   (owns the transaction; atomic-with-cleanup)
   ├─► StorageBackend.save(...) ──► storage_path                 [port → LocalFileStorage]
   ├─► sha256(bytes) ──► content_hash  (dedup check)
   ├─► DocumentParser.parse(path, type) ──► ParsedDocument       [port → Pdf/Text parsers]
   ├─► Chunker.split(text) ──► [Chunk(content, index, page?, token_count)]
   ├─► EmbeddingsProvider.embed_documents([...]) ──► [vector]    [port → GeminiEmbeddingsProvider]
   └─► DocumentRepository + ChunkRepository.persist(...)         ── Entity (db/repositories/)

POST /search (query)                                ── Boundary (api/search.py)
        ▼
RetrievalService.search(user_id, query, top_k, filters)         ── Control (services/retrieval.py)
   ├─► EmbeddingsProvider.embed_query(query) ──► vector          [RETRIEVAL_QUERY]
   └─► ChunkRepository.search_similar(user_id, vector, k, filters) ──► [ChunkMatch]
```

**Ports (ABCs) and Phase-2 adapters:**

| Port | Methods | Adapter(s) | Swap target |
|------|---------|-----------|-------------|
| `StorageBackend` | `save`, `delete` | `LocalFileStorage` (`UPLOAD_DIR/{user_id}/{uuid}_{name}`) | S3/object storage |
| `DocumentParser` | `parse` | `PdfParser` (pypdf + OCR fallback), `PptxParser` (python-pptx), `DocxParser` (python-docx), `TextParser` (utf-8), `ImageParser` (OCR), dispatched by sniffed content type | other formats |
| `OcrProvider` | `extract_text(image)` | `TesseractOcr` (pytesseract); `FakeOcrProvider` (tests) | cloud OCR (Vision/Document AI) |
| `EmbeddingsProvider` | `embed_documents`, `embed_query` | `GeminiEmbeddingsProvider` (`google-genai`); `FakeEmbeddingsProvider` (tests) | OpenAI/local |

`Chunker` (config-driven, not an ABC) applies recursive token splitting *within* each segment and
never merges across segment boundaries, attaching the segment's metadata (page/slide/heading) to
every chunk it produces. A short segment (e.g. a sparse slide) becomes a single chunk.

**Structured-segment contract:** `DocumentParser.parse` returns a `ParsedDocument` = an ordered
list of `Segment(text, page_number?, section?)` plus `page_count`, rather than one flat string.
Each adapter emits the boundaries it naturally knows:
- `PptxParser` → one segment per slide (`page_number` = slide #, `section` = slide title).
- `TextParser` (Markdown) → one segment per heading block (`section` = heading trail); plain TXT → one segment.
- `PdfParser` → one segment per page (`page_number` = page #).
- `DocxParser` → segments per heading where present, else whole document.
- `ImageParser` → a single OCR'd segment (`page_number` = 1).

**OCR fallback:** the handler sniffs the file's real type and the service selects the matching
adapter. `ImageParser` runs the image straight through `OcrProvider`. `PdfParser` extracts text
per page with `pypdf`; any page whose text is below `pdf_ocr_min_chars_per_page` is rasterized
(`pdf2image`) and sent to `OcrProvider`, so scanned and mixed text/scanned PDFs work. `page_count`
= PDF pages / PPTX slides / `1` for an image / `null` for DOCX/TXT/MD.

**Atomicity:** `IngestionService` writes the file, then creates the `documents` row + all
`document_chunks` in one DB transaction and commits last. On any failure it rolls back **and**
deletes the written file (compensating cleanup). Because success is all-or-nothing, no
`status` column is needed.

**Validation placement (per project conventions):** handler validates file type (by content
sniffing) + size (→ `400`/`413`) and parses optional metadata form fields; service enforces the
ingestion flow and dedup rule. Repositories stay CRUD-only.

---

## 4. Data model

One Alembic migration. The `vector` extension already exists (Phase 0).

### `documents` — one row per upload (the unit users manage)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK→users (CASCADE), indexed | per-user scoping |
| `filename` | str | original, sanitized |
| `title` | str, nullable | user-supplied or Phase-3 backfilled |
| `course` | str, nullable | user-supplied or Phase-3 backfilled |
| `tags` | JSONB, default `[]` | user-supplied or Phase-3 backfilled |
| `content_type` | str | from content sniffing |
| `content_hash` | str (sha256 hex) | **unique per `(user_id, content_hash)`** |
| `storage_path` | str | path on the volume |
| `file_size` | int | bytes |
| `page_count` | int, nullable | PDFs |
| `chunk_count` | int | chunks produced |
| `embedding_model` | str | provenance, e.g. `gemini-embedding-001` |
| `embedding_dimension` | int | e.g. `1536` |
| `created_at` | timestamptz | UTC |
| `updated_at` | timestamptz | UTC; supports Phase-3 metadata backfill |

### `document_chunks` — the searchable units

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `document_id` | UUID FK→documents (CASCADE), indexed | delete doc → chunks vanish |
| `user_id` | UUID FK→users (CASCADE), indexed | **denormalized** for join-free per-user search |
| `chunk_index` | int | order within document |
| `content` | text | chunk text |
| `token_count` | int, nullable | chunk size in tokens |
| `page_number` | int, nullable | PDF page / PPTX slide # — for citations (Phase 3) |
| `section` | str, nullable | heading trail / slide title from structure-aware parsing |
| `embedding` | `Vector(1536)` | pgvector column |
| `created_at` | timestamptz | UTC |

**Indexes / distance:** cosine (`vector_cosine_ops`, `<=>`). HNSW index on `embedding`; btree on
`user_id` and `document_id`; unique `(user_id, content_hash)` on documents. Search query shape:
`WHERE user_id = :me [AND course = :course ...] ORDER BY embedding <=> :qvec LIMIT :k`.

---

## 5. API & schemas

All endpoints require a valid access token (`get_current_user`, Phase 1) and only ever touch the
caller's rows.

| Endpoint | Request | Success | Errors |
|----------|---------|---------|--------|
| `POST /documents` | multipart `file` + optional `title`, `course`, `tags` | `201` `DocumentResponse` | `400` bad/empty/unsupported, `413` too large, `409` duplicate (returns existing doc ref) |
| `GET /documents` | optional `?course=` | `200` `list[DocumentResponse]` (newest first) | — |
| `DELETE /documents/{id}` | path id | `204` (cascade chunks + delete file) | `404` not yours / missing |
| `POST /search` | `{ query, top_k?=5, course?, tags? }` | `200` `list[ChunkMatch]` | `400` empty query |

**Schemas (Pydantic):**
- `DocumentResponse`: `id, filename, title, course, tags, content_type, page_count, chunk_count,
  file_size, embedding_model, embedding_dimension, created_at, updated_at`.
- `DocumentUploadMetadata` (parsed from form): `title?`, `course?`, `tags?: list[str]`.
- `SearchRequest`: `query: str` (non-empty), `top_k: int = 5` (bounded 1–20), `course?`, `tags?`.
- `ChunkMatch`: `chunk_id, document_id, filename, title, content, page_number, section, score`
  (cosine similarity), ordered most-relevant first.

**Ownership:** list/delete/search filter by `user_id = current_user.id`; user-isolation is tested.

**Why a standalone `/search` with no LLM:** it is the manual-test surface for retrieval this
phase. Phase 3's LangGraph calls the same `RetrievalService` internally; `/search` may be kept as
a debug endpoint or removed later.

---

## 6. Configuration

New `Settings` fields (all defaulted so existing setups keep working):

| Setting | Default | Purpose |
|---------|---------|---------|
| `embedding_model` | `gemini-embedding-001` | Gemini model id |
| `embedding_dimension` | `1536` | must match `Vector(1536)` |
| `embedding_doc_task_type` | `RETRIEVAL_DOCUMENT` | task type for chunks |
| `embedding_query_task_type` | `RETRIEVAL_QUERY` | task type for queries |
| `chunk_tokens` | `512` | chunk size (tokens) |
| `chunk_overlap_tokens` | `64` | overlap (tokens) |
| `upload_dir` | `./uploads` (`/data/uploads` in Docker) | file storage root |
| `max_upload_bytes` | `26214400` (25 MiB) | size cap |
| `allowed_content_types` | pdf, pptx, docx, txt, md, png, jpeg | sniffed-type allowlist |
| `retrieval_top_k` | `5` | default `/search` k |
| `ocr_enabled` | `True` | toggle OCR + PDF fallback |
| `ocr_language` | `eng` | Tesseract language |
| `pdf_ocr_min_chars_per_page` | `10` | below this, a PDF page is treated as scanned → OCR |
| `tesseract_cmd` | `None` | optional explicit path to the Tesseract binary (Windows dev) |

`google_api_key` already exists in `Settings` — Phase 2 is where it is first used.
**New Python deps:** `pypdf`, `python-pptx`, `python-docx`, `pytesseract`, `pdf2image`, `Pillow`,
`langchain-text-splitters`, `tiktoken`, `google-genai`.
**New system deps:** `tesseract-ocr` and `poppler` (for `pdf2image`) — installed in the Docker
image and required for local OCR dev (documented in the learning doc / README).

---

## 7. Testing (TDD, against `notes_rag_test`)

- **Unit:** `Chunker` — token sizing + overlap, **respects segment boundaries** (never merges
  across slides/pages, splits Markdown on headings, propagates `page_number`/`section` to chunks,
  a tiny segment stays one chunk); each parser adapter against a tiny fixture file
  (`PdfParser`, `PptxParser` per-slide segments, `DocxParser`, `TextParser` Markdown-heading
  segments, `ImageParser`); the PDF per-page OCR-fallback logic (mock `OcrProvider`, feed an
  empty-text page); content-sniff + filename
  sanitization (incl. distinguishing OOXML docx vs pptx, and rejecting disallowed types);
  `LocalFileStorage` save/delete; `FakeEmbeddingsProvider` (deterministic vectors); embedding
  L2-normalization. One real-Tesseract test on a generated image, **skipped if the binary is absent**.
- **Service:** `IngestionService.ingest` persists document + chunks (fake embeddings + fake OCR,
  temp dir);
  **failure path** rolls back the DB **and** deletes the file; duplicate upload → `409` and no new
  rows; `RetrievalService.search` returns chunks ordered by similarity and respects metadata filters.
- **Integration (API):** upload small `.txt` → `201`; list shows it (and `?course=` filters);
  search returns it ranked; delete → `204`, chunks gone, file gone; `401` without token;
  **user-isolation** (A cannot see/search/delete B's docs); duplicate upload → `409`.
- **pgvector ordering:** integration test with deterministic fake vectors asserting `<=>` order
  against the real `Vector(1536)` column.

---

## 8. Docker & ops

- Add a named volume mounted at `/data/uploads` on the backend service; set
  `UPLOAD_DIR=/data/uploads` so uploaded files survive restarts.
- Install `tesseract-ocr` and `poppler-utils` in the backend Dockerfile (and in the CI job) so
  OCR works in the container; document the Windows local-dev install (Tesseract + poppler, with
  the optional `TESSERACT_CMD` setting if the binary isn't on `PATH`).
- One new Alembic revision creating both tables, the `Vector(1536)` column, the HNSW index, and
  the btree + unique indexes.

---

## 9. Out of scope (deferred)

LLM answering, summary-vs-Q&A intent routing, LangGraph, citation rendering, background/async or
queue-based processing (a strong fast-follow given OCR latency), object storage, document
update/rename endpoints, `GET /documents/{id}` detail + original-file re-download, LLM-based
metadata auto-extraction (the *hook* exists via nullable metadata + `updated_at`; the extraction
itself is Phase 3), re-chunking/re-embedding on model change, cloud/handwriting-grade OCR
(Tesseract now; swappable via `OcrProvider`).

---

## 10. Risks / notes

- **pgvector dimension cap:** HNSW/IVFFlat index `vector` is limited to 2000 dims — 1536 stays
  safely under it. Choosing 3072 later would require `halfvec`.
- **Normalization:** Gemini only normalizes the full 3072-dim output; at 1536 the adapter must
  L2-normalize so cosine distance is meaningful.
- **Token counting:** tiktoken is an *approximation* of Gemini's tokenizer (no public Gemini
  tokenizer), used only to size chunks — exactness isn't required, only consistency.
- **Synchronous latency + OCR:** OCR of a multi-page scanned PDF can take tens of seconds, so a
  synchronous upload may feel slow or risk client timeouts. Acceptable for a single-user app this
  phase; background processing (the natural fix) is deferred. The slow steps (OCR, embedding) are
  already isolated behind ports, so the upgrade is contained.
- **System dependencies:** OCR needs the `tesseract` binary and `poppler` (for `pdf2image`) — a
  setup burden on Windows dev and in Docker/CI. Documented; `OCR_ENABLED=false` disables the path.
  (PyMuPDF could replace pypdf+poppler for PDF text+raster in one library, avoiding poppler, at
  the cost of an AGPL dependency — noted as a possible later simplification.)
- **OOXML sniffing:** DOCX and PPTX are both ZIP containers, so magic-byte sniffing must inspect
  the archive's content types (or combine sniff + extension) to tell them apart.
- **OCR accuracy:** Tesseract is weak on handwriting and messy scans; quality varies. The
  `OcrProvider` port allows swapping in cloud OCR later without touching the pipeline.
- **API key in CI:** tests mock `EmbeddingsProvider` and `OcrProvider`, so CI needs no Google key
  or network (the optional real-Tesseract unit test self-skips when the binary is absent).
