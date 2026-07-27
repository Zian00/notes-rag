# Ingestion Quality & Correctness — Design

**Status:** Draft, awaiting review
**Scope:** Backend (`backend/app/rag/*`, `backend/app/services/ingestion.py`, `backend/app/models/document.py`, `backend/app/api/documents.py`) + minor frontend (`frontend/src/components/documents/*`, `frontend/src/api/hooks/useDocuments.ts`)
**Precedes:** Retrieval-quality design (reranking, hybrid search, smarter rewrite) and Generation-correctness design (hallucination checking, citation fidelity) — see the roadmap note at the end of this doc.

## 1. Motivation

The current ingestion pipeline (documented in `docs/learning/02-ingestion-retrieval.md` and `docs/superpowers/specs/2026-06-15-ingestion-retrieval-design.md`) works, but has three known, explicitly-deferred limitations that directly affect answer quality and correctness:

1. **Chunking is naive.** Chunks are split purely by token count within whatever segment a parser produces. Some parsers (PPTX, DOCX, Markdown) already segment by heading; **PDF does not** — it segments by page, so a chunk can start mid-topic with no idea what section it belongs to.
2. **Dedup is exact-hash-only.** Re-uploading an edited version of an existing document (a typo fix, a corrected slide) is indistinguishable from an unrelated new document — full re-parse/re-chunk/re-embed, and the old version is orphaned, requiring manual cleanup.
3. **Ingestion is synchronous and blocks the request.** Parsing, chunking, and embedding all run inline inside the HTTP request handler. The Gemini embedding call is a **blocking** call sitting inside an `async def`, stalling the event loop for its duration. This gets worse as this design adds more ingestion-time work (semantic-chunking fallback, PDF structure extraction).

This design addresses all three together because they interact: making chunking heavier (semantic fallback) makes async processing more urgent; async processing changes how dedup/versioning has to be wired (the "Replace" flow needs the same background pipeline as fresh uploads).

## 2. Current state (verified against code)

| Format | Parser | Current segmentation |
|---|---|---|
| PDF | `PdfParser` (`backend/app/rag/parsing/pdf.py`) | One segment **per page** (`pypdf` text extraction + per-page OCR fallback via `pdf2image`). No heading awareness. |
| PPTX | `PptxParser` (`backend/app/rag/parsing/office.py:9-36`) | One segment **per slide**, `section` = slide title. Already structure-aware. |
| DOCX | `DocxParser` (`office.py:39-66`) | Segments split on "Heading N" paragraph styles (flush-on-heading pattern). Already structure-aware. |
| Markdown | `TextParser` (`backend/app/rag/parsing/text.py`) | Segments split on `#` lines (flush-on-heading, single flat heading, no nesting). Already structure-aware. |
| Plain text | `TextParser` | One segment for the whole file. No structure exists to extract. |
| Images | `ImageParser` | OCR'd whole-image text, one segment. No structure. |

`Chunker.split()` (`backend/app/rag/chunking.py:28-52`) never merges across segments, and **always** applies `RecursiveCharacterTextSplitter` (512 tokens, 64 overlap) within each segment regardless of whether the segment already fits in one chunk. Each chunk inherits its segment's `page_number`/`section`.

Dedup: `content_hash = sha256(whole file)`; exact match against `(user_id, content_hash)` → `409` with the existing `document_id` (`backend/app/services/ingestion.py:70-73`, `backend/app/db/repositories/document.py:33-40`). No partial/near-duplicate detection, no "update" path.

Ingestion is fully synchronous inside `POST /documents` (`backend/app/api/documents.py:20-64`): parse → chunk → embed (one blocking Gemini API call with all chunk texts) → persist, all in one request, wrapped in a DB savepoint with compensating file-delete on failure.

`Document` model (`backend/app/models/document.py:19-41`) has no processing-status field — a row only exists once ingestion has already fully succeeded.

## 3. Goals

- Chunks reflect the document's actual structure wherever that structure exists; fall back to meaning-based splitting where it doesn't.
- Re-uploading an edited version of a document is a fast, explicit "Replace" that only re-embeds what actually changed.
- Ingestion (including the new, heavier chunking work) never blocks the request thread; the API responds immediately and processing happens in the background with visible status.

## 4. Non-goals (explicitly deferred)

- Cross-document chunk/embedding reuse (only per-document reuse on Replace).
- Version history / rollback — replace is overwrite-only.
- Fuzzy/near-duplicate chunk matching — only exact content-hash reuse.
- Any retrieval-side changes (reranking, hybrid search) — that's the next design, once this ships.

## 5. Design — Part A: Structure-aware chunking with semantic fallback

### 5.1 Per-format changes

- **PDF (the real gap):** Replace `pypdf`-based extraction with **`pymupdf4llm`**, which converts the PDF directly to Markdown with inferred `#`/`##` headers from font-size/style signals. `PdfParser` is rewritten to run the existing flush-on-heading segmentation (the same pattern already used by `_split_markdown`/`DocxParser`) over `pymupdf4llm`'s Markdown output, instead of pypdf's flat per-page text. The existing per-page OCR fallback (`pdf2image` + `OcrProvider`) is preserved for pages `pymupdf4llm` can't extract usable text from (still detected via the existing `min_chars` heuristic, applied per-page before structure extraction).
- **PPTX, DOCX, Markdown:** No parser change needed — already segment by heading/slide.
- **Heading paths (nesting):** Where a format has genuine multi-level headings (Markdown `#`/`##`/`###`, DOCX "Heading 1/2/3", PDF via `pymupdf4llm`'s multiple header levels), upgrade the flush-on-heading logic from tracking a single flat `current_heading` string to a **heading stack**, so `section` can render as a breadcrumb (e.g. `"Lecture 4 > Neural Networks"`) instead of just the innermost heading. This applies to `TextParser`'s markdown path, `DocxParser`, and the new PDF path. **Not applicable to PPTX** (slides have no sub-heading concept).
- **Plain text, images:** Unchanged — genuinely no structure to extract; these always rely on the semantic fallback below.

### 5.2 The chunking cascade (rewrite of `Chunker.split()`)

For each segment produced by a parser:

1. **Fits within `chunk_tokens` (512)?** → keep as one chunk, no splitting. (New: today it always splits regardless.)
2. **Too large, no finer sub-structure available?** → run **semantic chunking**: split the segment into sentences, embed each sentence with a local model (via `fastembed`, see 5.3), compute cosine similarity between consecutive sentences, and cut a chunk boundary wherever similarity drops sharply (percentile-based breakpoint, the standard method).
3. **A semantic sub-chunk is still too large** (rare edge case) → fixed-size recursive splitting (`RecursiveCharacterTextSplitter`, same as today) as the last-resort safety net.

This preserves today's invariant that a chunk never spans two segments, while stopping the current behavior of always force-splitting a segment even when it would have fit as one coherent chunk.

### 5.3 Semantic chunking implementation

- Library: **`fastembed`** (Qdrant), running an ONNX model (`BAAI/bge-small-en-v1.5` or `all-MiniLM-L6-v2`) — chosen over `sentence-transformers` specifically to avoid adding PyTorch as a dependency on the small production VPS, and because `fastembed` also supports cross-encoder/rerank models via ONNX, keeping this consistent with the reranker the next (retrieval-quality) design will likely add.
- This is a **local, offline** model — no external API call, no added latency-sensitive network dependency, cost is purely local CPU time during ingestion (which is being moved to a background worker anyway, see Part C).
- New module: `backend/app/rag/semantic_chunking.py`.

## 6. Design — Part B: Dedup & versioning

### 6.1 New "Replace" action

A new endpoint, scoped to one existing document (e.g. `POST /documents/{document_id}/replace`), distinct from the existing upload endpoint (whose exact-hash 409 behavior is unchanged for genuinely new uploads).

**Flow:**
1. Compute the new file's whole-document SHA-256 hash **before** any parsing.
2. If it matches the document's **current** `content_hash` → short-circuit immediately: return "no changes detected," do no parsing/chunking/embedding at all.
3. Otherwise, parse and chunk the new version fully (cheap — CPU only, no API calls).
4. Compute a SHA-256 hash **per chunk** (new `content_hash` column on `DocumentChunk`).
5. Compare the new chunk-hash set against the old document's own chunk-hash set (not positional — a hash-set membership check, so reordered/moved sections still match):
   - Hash found in old set → **reuse that chunk's existing embedding**, no embedding API call.
   - Hash not found → new/changed content → needs a fresh embedding call.
6. Any old chunk whose hash isn't in the new set → deleted.
7. Document metadata (`content_hash`, `chunk_count`, `updated_at`, etc.) updated to reflect the new version.

**Overwrite-only** — no version history/rollback table. A cross-document duplicate (Replace with content identical to some *other* document) is deliberately not special-cased — it just re-embeds normally since diffing only compares against the same document's own prior chunks.

### 6.2 Frontend

A "Replace" action on each document row (`frontend/src/components/documents/DocumentRow.tsx`) opens an upload scoped to that `document_id`, using a new `useReplaceDocument` hook alongside the existing `useUploadDocument`/`useDeleteDocument` (`frontend/src/api/hooks/useDocuments.ts`).

## 7. Design — Part C: Async/background processing

### 7.1 Task queue: `procrastinate`

Chosen over Celery/`arq` because it's **Postgres-backed** — no new service (Redis, broker) added to the deployment, which matters given the small single-VPS Phase 5 target. Adds a worker process (run alongside the API, e.g. as a second container/process in the compose stack) and a `procrastinate` schema/tables in the existing Postgres database.

### 7.2 New document status field

`Document` gets a new `status` column (migration required): `pending → processing → ready | failed`.

- `POST /documents` and `POST /documents/{id}/replace` do only the **fast, synchronous** part: validate (size/content-type sniff), compute whole-file hash (dedup/short-circuit check), persist the raw file, write/update the document row with `status="pending"`, enqueue a `procrastinate` job, and return immediately (the row already has an id the client can poll/reference).
- The background task does the heavy work: parse → chunk (including the new semantic-fallback path) → diff (for Replace) → embed → persist chunks → flip `status` to `ready`.
- On any failure inside the task (parse error, embedding API error), `status` flips to `failed` and the error is recorded; the user can retry or delete.

### 7.3 Frontend

- `useDocuments` (`frontend/src/api/hooks/useDocuments.ts`) adds a `refetchInterval` (TanStack Query, already used elsewhere in the app) active only while any document in the list is `pending`/`processing`, so the list naturally refreshes to `ready`/`failed` without new real-time infrastructure (no websockets).
- `DocumentRow.tsx` shows a status badge for non-`ready` documents (processing spinner / failed state with a retry or delete action).

## 8. Data model changes

- `documents.status`: new enum/string column (`pending` / `processing` / `ready` / `failed`), migration + backfill existing rows to `ready`.
- `documents`: nullable `error_message` column — required so a `failed` status is actionable (the user needs to know why before deciding to retry or delete, per §7.2).
- `document_chunks.content_hash`: new indexed column (SHA-256 of `content`), used for the Replace diffing in Part B.

## 9. API changes

- `POST /documents/{document_id}/replace` — new endpoint (Part B).
- `POST /documents` — response changes to reflect `status="pending"` immediately rather than a fully-processed document (Part C).
- `GET /documents` — response schema gains `status` (and `error_message` when `failed`).

## 10. Testing strategy

Following the existing TDD pattern (`backend/tests/test_*`):
- Chunking cascade: per-format structure detection (including the new PDF path), semantic fallback triggering only when a segment is oversized with no sub-structure, fixed-size safety net still reachable.
- Dedup/versioning: short-circuit on identical Replace, correct reuse-vs-reembed split on partial edits, deletion of removed chunks, cross-document duplicate is *not* specially reused (documented non-goal).
- Async: job enqueued correctly, status transitions (`pending→processing→ready`/`failed`), failure surfaces `error_message`, retry path works.

## 11. Out of scope for this design (see §4)

Cross-document reuse, version history, fuzzy matching, and anything on the retrieval/generation side (next two designs in this sequence).

## 12. Roadmap context

This is the first of three sequential designs the user asked to tackle one at a time:
1. **Ingestion quality** (this document)
2. Retrieval quality (reranking, hybrid search, smarter query rewrite)
3. Generation correctness (hallucination checking, citation fidelity — including a known bug where `get_document_content`'s tool output mislabels `title`/`filename` for whole-document-summary citations)
