# Phase 2 — Ingestion & Retrieval: Concepts

This guide explains the ingestion and retrieval system we built and *why* each piece works
the way it does. It's accurate to the code in `backend/app/` (services, rag, db, api).

---

## The big picture

Phase 2 is the **data plane** — it turns an uploaded file into searchable vector data, and
turns a text query into a ranked list of relevant chunks. There is no LLM here, no chat, no
summarisation. Phase 3 (LangGraph) will sit on top of this and use the same retrieval service
to answer questions; Phase 2 just makes the data ready.

**Ingestion assembly line:**

```
POST /documents (multipart upload)          ── api/documents.py (boundary)
        │  content-sniff type, check size
        ▼
IngestionService.ingest(...)                ── services/ingestion.py (control)
   │   (owns the DB transaction; atomic-with-cleanup)
   ├─► sha256(bytes) ──► content_hash ──► dedup check (409 if duplicate)
   ├─► StorageBackend.save(...)    ──► raw file on disk
   ├─► ParserDispatcher.parse(...)  ──► ParsedDocument [Segment, ...]
   ├─► Chunker.split(...)           ──► [Chunk(content, index, page, section, tokens)]
   ├─► EmbeddingsProvider.embed_documents([...]) ──► [[float, ...], ...]
   └─► DocumentRepository + ChunkRepository (persist to Postgres/pgvector)
```

**Retrieval path:**

```
POST /search                                ── api/search.py (boundary)
        ▼
RetrievalService.search(user_id, query, top_k, ...)   ── services/retrieval.py (control)
   ├─► EmbeddingsProvider.embed_query(query) ──► [float, ...]
   └─► ChunkRepository.search_similar(user_id, vector, k) ──► [ChunkSearchResult]
               uses pgvector  <=>  (cosine distance) + HNSW index
```

Handlers contain no business logic. Services own transactions. Repositories do CRUD only.
Every external dependency is injected — same class for tests (with fakes) and production.

---

## Ports & adapters here

The "hexagonal" style from Phase 0 shows up concretely in four swappable seams:

| Port (ABC) | Methods | Phase-2 adapter | Could swap to |
|---|---|---|---|
| `StorageBackend` | `save`, `delete` | `LocalFileStorage` (`rag/storage.py`) | S3, GCS, Azure Blob |
| `DocumentParser` (each adapter) | `parse(data, content_type) -> ParsedDocument` | `PdfParser`, `PptxParser`, `DocxParser`, `TextParser`, `ImageParser` | New formats (audio, HTML…) |
| `OcrProvider` | `extract_text(image) -> str` | `TesseractOcr` (`rag/ocr.py`) | Cloud Vision API, Document AI |
| `EmbeddingsProvider` | `embed_documents`, `embed_query` | `GeminiEmbeddingsProvider` (`rag/embeddings.py`) | OpenAI, local model |

All four are injected into the services via FastAPI's `Depends` mechanism (`api/deps.py`).
Tests pass a `FakeEmbeddingsProvider` (deterministic short vectors) and a `FakeOcrProvider`
(returns canned text) — no API key, no network, no binary needed for the test suite.

**Why no `VectorStore` port?** The spec made a deliberate choice: we're committed to
pgvector, so a port over one concrete store would be YAGNI ("you aren't gonna need it").
The swappable seam for embeddings is `EmbeddingsProvider`; vector *storage* lives in
`ChunkRepository` alongside the rest of the DB layer. This keeps the code simpler.

**`ParserDispatcher`** (`rag/parsing/__init__.py`) is not an ABC itself — it's a concrete
router that holds all the parser adapters and selects by (already-sniffed) content type.
The individual adapters (`PdfParser`, `PptxParser`, etc.) are the swappable implementations.

---

## Content sniffing

Before any parsing happens, the handler sniffs the *real* content type from the file's bytes
(`utils/files.py → sniff_content_type`), ignoring whatever MIME type the client declared.

- PDF: magic bytes `%PDF`
- PNG / JPEG: magic bytes `\x89PNG` / `\xFF\xD8\xFF`
- PPTX / DOCX: both are ZIP archives (`PK\x03\x04`); the function opens the ZIP and checks
  whether internal paths start with `ppt/` or `word/` to tell them apart
- TXT / MD: no magic bytes — these are accepted by extension (`.txt` / `.md`) only if the
  bytes decode as valid UTF-8

This prevents a user mislabelling a file (accidentally or maliciously) from confusing the
parser.

---

## Parsing and the structured-segment contract

Rather than handing the pipeline one flat string, each parser returns a **`ParsedDocument`**
— an ordered list of **`Segment`** objects plus an optional `page_count`
(`rag/types.py`). A `Segment` carries:

- `text` — the extracted text for that structural unit
- `page_number` — PDF page or PPTX slide number (nullable for DOCX / TXT / MD)
- `section` — heading trail or slide title (nullable where not available)

This structured contract is the key to coherent chunking (see next section). Each format
adapter emits the boundaries it naturally knows:

| Parser | Segment boundary | `page_number` | `section` |
|---|---|---|---|
| `PdfParser` | one segment per PDF page | page # | — |
| `PptxParser` | one segment per slide (all text frames combined) | slide # | slide title |
| `DocxParser` | one segment per heading-styled paragraph | — | heading text |
| `TextParser` (TXT) | whole document = one segment | — | — |
| `TextParser` (Markdown) | one segment per `#` heading block | — | heading text |
| `ImageParser` | whole image = one segment | 1 | — |

### PDF OCR fallback

`PdfParser` (`rag/parsing/pdf.py`) tries the embedded text layer first using `pypdf`. Any
page whose extracted text is shorter than `pdf_ocr_min_chars_per_page` (default: 10 chars)
is treated as a scanned image — `pdf2image` rasterizes just that single page (requires the
`poppler` system library), then `OcrProvider.extract_text` runs Tesseract on it.

This per-page fallback correctly handles:
- text-only PDFs (no OCR needed at all)
- fully scanned PDFs (every page OCR'd)
- mixed PDFs (some text pages, some scanned — each handled appropriately)

`ImageParser` (`rag/parsing/image.py`) sends the entire image straight to `OcrProvider` as
a single segment.

**System requirements for OCR:** the `tesseract` binary and `poppler` must be installed on
the host (or in Docker). Set `OCR_ENABLED=false` to disable the OCR path entirely if these
are not available. On Windows, set `TESSERACT_CMD` to the explicit binary path if it's not
on `PATH`.

---

## Structure-aware chunking

`Chunker` (`rag/chunking.py`) works segment by segment — it **never merges** across
segment boundaries. For each segment it runs a **recursive token splitter**
(`RecursiveCharacterTextSplitter.from_tiktoken_encoder`, from `langchain-text-splitters`)
configured at 512 tokens per chunk with 64-token overlap.

"Recursive" means the splitter tries to break on natural boundaries in order — paragraph
breaks, then sentence breaks, then words — before doing a hard cut. This keeps chunks
semantically whole even when they must be split.

Every chunk inherits the segment's `page_number` and `section` metadata. A short segment
(e.g. a sparse slide with only a title) that's already under 512 tokens becomes a single
chunk as-is.

**Why token-based sizing?** Embedding models have a token budget, not a character budget.
A 512-token chunk is a meaningful fraction of a model's context window regardless of how
many raw characters it contains.

**Why tiktoken?** The `cl100k_base` tiktoken encoder is an *approximation* of Gemini's
tokenizer (Gemini's exact tokenizer isn't public), used only to size chunks consistently.
Exactness isn't required — we just need chunks to be consistently sized so no chunk
overflows the model.

**Why 64-token overlap?** A sentence or key idea near the boundary of one chunk will also
appear at the start of the next, so retrieval doesn't miss it due to an arbitrary cut point.

**What's deferred:** smarter chunking methods — semantic chunking, small-to-big retrieval,
contextual retrieval — are Phase 3 improvements. The structure-aware approach here is a
significant step up from blind fixed-size character splitting.

---

## Embeddings

An **embedding** is a list of numbers (a vector) that represents the *meaning* of a piece
of text. Texts with similar meanings produce vectors that are geometrically close to each
other. That's what makes semantic search work: "lecture notes on photosynthesis" and
"chloroplasts absorb sunlight" end up near each other in vector space.

### Why `gemini-embedding-001` at 1536 dimensions?

`text-embedding-004` (768 dims) was deprecated on 2026-01-14. `gemini-embedding-001` is the
current GA model at the time of writing. It natively produces 3072-dimensional vectors but
supports **Matryoshka** truncation — you can ask for fewer dimensions (we use 1536) without
dramatically losing quality, just cutting the vector short.

1536 was chosen because:
- pgvector's HNSW index supports up to 2000 dimensions — 1536 stays safely under that limit
- It's a good balance of quality vs. storage (each embedding is ~6 KB in float32)

### Why L2-normalize?

When Gemini truncates to 1536 dimensions, the resulting vector is **not** automatically
unit-length. The `<=>` cosine distance operator in pgvector assumes unit vectors for its
math to be correct. `l2_normalize` in `rag/embeddings.py` divides each component by the
vector's magnitude, so all stored vectors are on the unit sphere and cosine distance gives
valid similarity scores.

### Asymmetric task types

`GeminiEmbeddingsProvider` uses two different task type hints:

| Use case | Task type | Method |
|---|---|---|
| Embedding document chunks at ingestion time | `RETRIEVAL_DOCUMENT` | `embed_documents` |
| Embedding a search query | `RETRIEVAL_QUERY` | `embed_query` |

Using different task types for documents vs. queries is called **asymmetric embedding**. The
model is tuned to produce vectors that compare well across the asymmetry — a query vector
for "what is photosynthesis?" will be close to a document chunk explaining it, even though
the two texts look very different. Using the same task type for both would give slightly
worse retrieval quality.

---

## pgvector and similarity search

The `DocumentChunk` model (`models/document.py`) has an `embedding` column declared as
`Vector(1536)` (from the `pgvector` SQLAlchemy extension). This is a native Postgres vector
column — not a text blob, not JSONB.

### The HNSW index

```sql
-- Created by the Alembic migration
CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops);
```

Without this index, a similarity query would scan every row and compute the distance to
each one — an O(N) full scan. HNSW (Hierarchical Navigable Small World) is an approximate
nearest-neighbour index that trades a tiny amount of recall for very fast retrieval. It's
the standard choice for production pgvector deployments.

### The search query

`ChunkRepository.search_similar` (`db/repositories/chunk.py`) runs approximately:

```sql
SELECT chunk_id, document_id, filename, title, content, page_number, section,
       embedding <=> :query_vector AS distance
FROM document_chunks
JOIN documents ON document_chunks.document_id = documents.id
WHERE document_chunks.user_id = :user_id
  [AND documents.course = :course]   -- optional filter
  [AND documents.tags @> :tags]      -- optional JSONB containment filter
ORDER BY distance ASC
LIMIT :top_k;
```

`<=>` is pgvector's cosine distance operator. Distance 0 means identical direction (perfect
match); distance 2 means opposite. The repository converts it to a **similarity score**
(`score = 1.0 - distance`) so higher scores mean more relevant results.

Per-user isolation is enforced at the SQL level: `WHERE document_chunks.user_id = :user_id`
is on every query — user A's chunks cannot appear in user B's search results.

---

## File storage

`LocalFileStorage` (`rag/storage.py`) writes the raw uploaded file to disk under:

```
{UPLOAD_DIR}/{user_id}/{uuid4_hex}_{safe_filename}
```

For example: `/data/uploads/3fa85f64-.../a1b2c3d4_lecture01.pdf`

The **path** is stored in the `documents.storage_path` column. The file bytes are never
stored in Postgres — that would balloon the database and make backups very large. Postgres
holds metadata (and the computed embeddings); the filesystem holds the originals.

The `{user_id}/` subdirectory keeps each user's files isolated at the OS level. The UUID
prefix on the filename avoids collisions when two users upload files with the same name.

**Why a `StorageBackend` port?** The current adapter writes to a local volume. Because all
file access goes through the two-method interface (`save` / `delete`), swapping to S3 or
another object store later requires only a new adapter class and a config change — the
ingestion and deletion code doesn't change.

### Storage growth

A rough guide to what grows as users upload more content:

| What | Approx size |
|---|---|
| Each embedding (1536 float32s) | ~6 KB in Postgres |
| Chunk text (≤512 tokens) | 0.5–2 KB |
| Original file (on disk) | varies (up to 25 MiB per upload) |

A document with 100 chunks costs roughly 800 KB of embedding storage in Postgres.

---

## Atomic ingestion and deduplication

### Deduplication

Before any file is written or parsed, `IngestionService.ingest` computes a `sha256` hash of
the raw bytes and queries `DocumentRepository.get_by_user_and_hash`. If a row already exists
for `(user_id, content_hash)`, it raises `DuplicateDocument` immediately and returns `409
Conflict` — no file is written, no parsing happens, no vectors are computed. The response
includes the existing document's id.

This is **per-user** deduplication: the same file uploaded by two different users is
ingested twice (each user owns their copy). The same file uploaded by the same user twice is
rejected on the second attempt.

### Atomicity — savepoint + compensating cleanup

The challenge: the file is written to disk *before* the DB transaction (because the storage
path must be known when creating the `documents` row). If the DB side then fails partway
through — a parsing error, an embedding API failure, a DB constraint violation — we'd be
left with an orphan file on disk and no corresponding DB row.

`IngestionService` handles this with a **savepoint** and **compensating cleanup**:

```python
storage_path = self._storage.save(user_id, filename, data)   # file written here
try:
    async with self._session.begin_nested():   # savepoint wraps all DB writes
        parsed = self._parser.parse(data, content_type)
        chunks = self._chunker.split(parsed)
        vectors = self._embeddings.embed_documents([c.content for c in chunks])
        document = await self._documents.create(...)   # documents row
        await self._chunks.add_many([...])             # chunk rows
except Exception:
    self._storage.delete(storage_path)   # compensating cleanup: remove the orphan file
    raise
await self._session.commit()             # only reached if everything above succeeded
```

The savepoint (`begin_nested`) rolls back *all* the DB writes if anything inside fails.
The `except` block then deletes the file that was already written. Either **everything
succeeds** (file on disk + document row + chunk rows all committed) or **nothing persists**
(DB rolled back + file deleted). No half-ingested documents, no orphan files.

A savepoint (rather than a full session rollback) is used so that unrelated objects loaded
elsewhere in the same SQLAlchemy session aren't expired by the rollback.

---

## Per-user isolation

Every table that holds user data (`documents`, `document_chunks`) has a `user_id` foreign
key. Every query in every repository filters by `user_id`. The key properties:

- `GET /documents` returns only the caller's documents (`list_for_user(current_user.id, ...)`)
- `DELETE /documents/{id}` uses `get_for_user(document_id, current_user.id)` — if the ID
  exists but belongs to another user, it returns `404` (not `403`), revealing nothing
- `POST /search` passes `user_id` through to `ChunkRepository.search_similar`, which has
  `WHERE document_chunks.user_id = :user_id` baked in

`user_id` is denormalized onto `document_chunks` (it could be derived via a join to
`documents`) to keep the similarity search query fast — one `WHERE` clause on an indexed
column, no extra join required.

---

## Why a standalone `/search` with no LLM

`POST /search` embeds your query, queries pgvector, and returns the raw chunks with
similarity scores. There's no LLM reading those chunks and writing an answer.

This is deliberate for Phase 2. It serves two purposes:

1. **Verification:** you can manually confirm that ingestion worked and retrieval is sane
   before adding the complexity of a language model. If the top results are clearly
   irrelevant, the chunking or embedding is broken — easy to diagnose at this layer.

2. **Reuse:** Phase 3's LangGraph agent will call `RetrievalService.search` directly (not
   via HTTP). The `/search` endpoint exposes the same service through the API boundary.
   It may be kept as a debug endpoint or removed once Phase 3 is running.

---

## Endpoints recap

| Endpoint | Auth | Purpose | Key responses |
|---|---|---|---|
| `POST /documents` | Bearer access token | Upload a file; runs full ingestion pipeline | `201` doc created; `400` bad/unsupported; `409` duplicate (returns existing id); `413` too large |
| `GET /documents` | Bearer access token | List caller's documents; optional `?course=` filter | `200` list |
| `DELETE /documents/{id}` | Bearer access token | Delete a document, its chunks, and its file | `204`; `404` not found / not yours |
| `POST /search` | Bearer access token | Semantic search over caller's chunks; optional `course`, `tags` filters | `200` list of `ChunkMatch` with `score` |

---

## How to test it

### Automated tests (no API key, no binaries needed)

```bash
make test
```

Tests use `FakeEmbeddingsProvider` (returns deterministic short vectors) and
`FakeOcrProvider` (returns canned strings). The optional real-Tesseract unit test is skipped
automatically if the binary is absent. No Google API key is needed; no network calls are
made.

### Live end-to-end

1. **Install system dependencies** (required for OCR):
   - Linux/Docker: `apt-get install tesseract-ocr poppler-utils`
   - Windows: install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and
     [poppler for Windows](https://github.com/oschwartz10612/poppler-windows); add both
     to `PATH`, or set `TESSERACT_CMD` in your `.env` to the exact binary path
   - Set `OCR_ENABLED=false` to skip OCR entirely

2. **Set your Google API key** in `.env`:
   ```
   GOOGLE_API_KEY=your_key_here
   ```

3. **Start the stack:**
   ```bash
   make dev
   ```

4. **Open the interactive docs:** `http://localhost:8000/docs`

5. **Try the flow:**
   - `POST /auth/register` → `POST /auth/login` → click **Authorize** with the access token
   - `POST /documents` — upload a `.txt`, `.pdf`, or `.pptx` file
   - `GET /documents` — confirm it's listed with `chunk_count` populated
   - `POST /search` — send `{ "query": "..." }` matching something in the file; inspect the
     `score` and `section` fields on the returned chunks
   - `DELETE /documents/{id}` — confirm it disappears from the list and search returns nothing
