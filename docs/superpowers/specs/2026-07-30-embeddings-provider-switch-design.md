# Embeddings Provider Switch: Gemini → OpenAI — Design

**Status:** Ready for implementation
**Scope:** Backend (`backend/app/rag/embeddings.py`, `backend/app/core/config.py`, `backend/app/api/deps.py`, `backend/app/jobs/ingestion_tasks.py`, `backend/app/main.py`, `backend/pyproject.toml`)
**Precedes:** Nothing — this is a standalone infrastructure fix, not part of the retrieval-quality sequence (reranking → hybrid search → query rewrite).

## 1. Problem Statement

Document ingestion embeds every chunk via Gemini's `embed_content` API (`GeminiEmbeddingsProvider`, `backend/app/rag/embeddings.py`). Two failures surfaced in quick succession while testing a real upload (`Topic1 ER(2).pdf`, 1.3 MB):

1. **400 INVALID_ARGUMENT** — Gemini's `batchEmbedContents` endpoint rejects any single call carrying more than 100 texts. The chunker produced more than 100 chunks for this document, and `embed_documents()` sent them all in one call. (Already fixed separately: `embed_documents()` now splits into ≤100-item sub-batches.)
2. **429 RESOURCE_EXHAUSTED** — even after batching correctly, Gemini's **free tier** caps embedding requests at 100/minute per project. A single large document needing several batch calls, especially alongside other activity, can exhaust this quota mid-ingestion, permanently failing the document (`status: failed`, `0 chunks`).

The user has OpenAI credits and wants to move off Gemini's free-tier limits entirely for embeddings, rather than building retry/backoff logic to work around them.

## 2. Solution

Add `OpenAIEmbeddingsProvider` as a second implementation of the existing `EmbeddingsProvider` interface, selectable via a new `embedding_provider` setting (mirroring the existing `llm_provider` pattern already used for the chat model). Default the setting to `"openai"` so it becomes the day-to-day provider going forward, while keeping `GeminiEmbeddingsProvider` available and swappable back via config — no code change needed to revert.

## 3. Current state (verified against code)

- `EmbeddingsProvider` (`backend/app/rag/embeddings.py`) is an abstract interface (`embed_documents(texts) -> list[list[float]]`, `embed_query(text) -> list[float]`) with exactly one adapter: `GeminiEmbeddingsProvider`.
- `GeminiEmbeddingsProvider` is constructed directly (`GeminiEmbeddingsProvider(settings)`) at three call sites: `backend/app/api/deps.py` (`get_embeddings()`), `backend/app/jobs/ingestion_tasks.py` (both `process_document` and `process_document_replace` tasks), and `backend/app/main.py` (lifespan, for the RAG graph's retrieval tool).
- `Settings.embedding_model` currently defaults to `"gemini-embedding-001"`; `Settings.embedding_dimension` defaults to `1536` and is hard-coded into the DB schema (`DocumentChunk.embedding: Vector(EMBEDDING_DIM)`, `EMBEDDING_DIM = 1536` in `backend/app/models/document.py`, migration-fixed).
- The LLM side already has an established multi-provider precedent: `Settings.llm_provider: Literal["google", "anthropic", "openai_compatible"]` plus a `build_chat_model(settings)` factory (`backend/app/rag/llm.py`) that branches on the setting. The `"openai_compatible"` branch lazily imports `langchain_openai.ChatOpenAI` inside the branch, raising a helpful `ImportError` ("needs `uv add langchain-openai`") if the package isn't installed — because it's a rarely-used local-LLM fallback, not the default path.
- `google-genai` is a **hard** dependency in `backend/pyproject.toml` (not lazy) — it's used unconditionally by the current, only embeddings provider.
- Dev DB currently has 2 documents with `status: ready` (embedded via Gemini) and 1 with `status: failed` (the document that triggered this investigation, 0 chunks, needs re-uploading regardless of provider).

## 4. Goals

- Ingestion no longer depends on Gemini's free-tier rate limits for embeddings.
- Embeddings provider is config-selectable (`google` | `openai`), not a hard rip-and-replace, consistent with how the LLM provider is already selectable.
- No pgvector schema/migration changes — the new provider's output dimension must match the existing `1536`-dim column.

## 5. Non-goals (explicitly deferred)

- **Retry-with-backoff for rate limits** — not built for either provider. OpenAI's tiers are expected to be generous enough (paid credits) that this class of failure shouldn't recur; if it does, it's a small, separately-scoped follow-up.
- **Automated re-embedding of existing Gemini-embedded chunks** — the 2 existing `ready` documents will be manually replaced/re-uploaded by the user after this ships. No migration script. (Mixing embedding spaces across providers is not supported — a chunk's embedding must come from whichever provider is currently configured, since cosine similarity is only meaningful within one provider's vector space.)
- **Removing `GeminiEmbeddingsProvider`** — it stays as a fully supported, switchable-back option.

## 6. Implementation Decisions

### 6.1 Model selection

`OpenAIEmbeddingsProvider` uses **`text-embedding-3-small`** — its native output dimension is 1536, exactly matching the existing `EMBEDDING_DIM`/`embedding_dimension` schema, so no truncation parameter and no DB migration are needed. (Contrast with `text-embedding-3-large`, native 3072-dim, which would require passing an explicit truncation parameter to reach 1536 — rejected as unnecessary extra complexity and cost for this use case, especially since hybrid search + reranking already compensate for embedding-model precision differences downstream.)

### 6.2 Config shape

New settings on `Settings` (`backend/app/core/config.py`), mirroring the existing `llm_provider`/`anthropic_api_key` pattern:

- `embedding_provider: Literal["google", "openai"]`, **default `"openai"`** — this is a deliberate default flip away from Gemini, since the whole point of this change is that OpenAI becomes the day-to-day provider. A fresh `.env`, a new dev machine, or the Phase 5 VPS deployment should not silently fall back to the provider being moved away from.
- `openai_api_key: str = ""` — same shape as the existing `anthropic_api_key: str = ""`.
- `embedding_model` stays as the single source of truth for which model name to request, but its meaning becomes provider-relative (a Gemini model name when `embedding_provider="google"`, an OpenAI model name when `embedding_provider="openai"`). Default value updates to `"text-embedding-3-small"` to match the new default provider.

### 6.3 Provider factory

A new `build_embeddings_provider(settings) -> EmbeddingsProvider` function, co-located in `backend/app/rag/embeddings.py` (alongside the provider classes it selects between — unlike `build_chat_model`, which lives in its own `llm.py` file because there's no single "LLM classes" module to co-locate it with). Branches on `settings.embedding_provider`:
- `"google"` → constructs `GeminiEmbeddingsProvider(settings)` (unchanged).
- `"openai"` → constructs `OpenAIEmbeddingsProvider(settings)`.

All three current construction call sites (`deps.py`'s `get_embeddings()`, both tasks in `ingestion_tasks.py`, `main.py`'s lifespan) switch from directly instantiating `GeminiEmbeddingsProvider` to calling this factory.

### 6.4 `OpenAIEmbeddingsProvider`

Implements the same `EmbeddingsProvider` interface (`embed_documents`, `embed_query`). Uses the official `openai` Python SDK. Since OpenAI's embeddings endpoint has its own per-call item limits (distinct from Gemini's), `embed_documents()` applies the same batching-into-sub-batches safety pattern already fixed in `GeminiEmbeddingsProvider.embed_documents()` — the exact sub-batch size is an OpenAI-specific constant, determined from OpenAI's documented per-request limits at implementation time (not necessarily the same `100` used for Gemini).

No re-normalization step is needed unless OpenAI's API returns non-unit-length vectors at the requested dimension (Gemini needed `l2_normalize()` specifically because it only unit-normalizes its full 3072-dim output before truncation; OpenAI's `text-embedding-3-small` is requested at its native 1536 dimension, so this should be verified during implementation rather than assumed).

### 6.5 Dependency

`openai` is added as a **hard** dependency in `backend/pyproject.toml` (via `uv add openai`), not a lazy/optional import — because it's now the default, everyday embeddings path, unlike `langchain-openai`'s lazy treatment for the rarely-used `openai_compatible` LLM fallback. Every `uv sync` should have it available without a manual extra install step.

## 7. Testing Decisions

Following the existing pattern in `backend/tests/test_embeddings.py` (which already tests `GeminiEmbeddingsProvider.embed_documents()`'s batching behavior by monkeypatching the underlying SDK client's method and asserting on call counts/ordering via the public interface — never reaching into private implementation details beyond what's needed to intercept the network call):

- **`OpenAIEmbeddingsProvider`**: construct with a `Settings` instance carrying a fake API key, monkeypatch the OpenAI client's embeddings-creation method, and verify (a) `embed_documents()` batches correctly when given more texts than the provider's per-call limit, preserving order; (b) `embed_query()` returns a single vector; (c) returned vectors are at the expected 1536 dimension.
- **`build_embeddings_provider(settings)`**: verify it returns a `GeminiEmbeddingsProvider` instance when `embedding_provider="google"` and an `OpenAIEmbeddingsProvider` instance when `embedding_provider="openai"`.
- Not tested directly (per existing repo convention — thin DI wiring in `deps.py` isn't independently unit-tested elsewhere): the three call sites' switch from direct construction to calling the factory.

## 8. Out of scope for this design (see §5)

Retry-with-backoff for rate limits (either provider); automated re-embedding of existing chunks; removing Gemini as an option.

## 9. Further notes

- This design was triggered by two real errors during manual testing, not a planned roadmap item — see the conversation history around 2026-07-30 for the original `400`/`429` error payloads.
- The existing-data gotcha (mixing embedding spaces across providers silently produces meaningless similarity scores) is a general invariant worth remembering for any *future* embeddings-model change too, not just this one: switching `embedding_model` or `embedding_provider` always implicitly requires re-embedding every existing chunk, or accepting degraded search quality for old documents until they're replaced.
