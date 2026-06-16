# Phase 3 — Agentic RAG / LangGraph: Design Spec

**Date:** 2026-06-16
**Status:** Approved
**Phase:** 3 of 5 (see roadmap in `docs/superpowers/specs/2026-06-13-foundation-design.md`)

---

## 1. Context

Phases 0–2 are complete: a FastAPI + Postgres/pgvector backend with per-user JWT auth, document
ingestion (parse → chunk → embed → store), and a plain semantic `/search` that returns the nearest
chunks. **There is still no LLM in the loop** — `/search` stops at "here are the relevant chunks."

Phase 3 puts an LLM *on top of* that retrieval so the app can actually **answer questions about a
user's notes, with citations, and remember the conversation**. The flow becomes:

> question → (LLM decides to search) → retrieve → grade relevance → (rewrite & re-retrieve if weak)
> → grounded, cited answer → streamed to the client → remembered for follow-ups

This is built as a **LangGraph** graph (nodes = steps, edges = control flow). It combines two
patterns the user explicitly chose to merge:

- **Agentic (tool-calling):** the LLM holds retrieval as a *tool* and decides whether and how often
  to call it — handles vague or multi-part questions.
- **Corrective (self-RAG):** a grading step checks whether retrieved chunks are actually relevant;
  if weak, it rewrites the query and retrieves again (bounded). This is the anti-hallucination guard
  — when the notes don't contain the answer, the graph *knows* and says so instead of inventing one.

### Phase 3 goal

A user can ask a natural-language question and get a **grounded, cited answer drawn only from their
own notes**, streamed token-by-token, inside a **persisted multi-turn conversation** (follow-up
questions keep context). It can also **summarise** — both a *topic* across notes ("summarise what my
notes say about heaps") and a *whole document* ("summarise my Lecture 5 notes"). The generation LLM
is **swappable by config** (Gemini default; Claude or a local/OpenAI-compatible model drop-in).
Running, tested, documented.

### Definition of done

- `POST /chat` answers a question grounded in the caller's notes, **streams tokens via SSE**, ends
  with a **citations** event, and persists the turn into a conversation; a follow-up question in the
  same conversation has prior context (checkpointer round-trip works).
- The graph **retrieves → grades → rewrites on weak retrieval (bounded by a retry cap)** and
  **refuses to hallucinate** when the notes lack the answer ("I couldn't find this in your notes").
- The agent has **three tools** — `retrieve_notes` (semantic Q&A + topic summaries), `list_documents`
  ("what notes do I have?" + resolve a named document), and `get_document_content` (fetch a whole
  document, in order, for whole-document summaries).
- An **`AGENTIC_RETRIEVAL` flag** (default on) lets a weak/local LLM fall back to a deterministic
  always-retrieve path (skip the tool-call *decision*), preserving grade → rewrite → generate.
- The generation LLM is selected by an `LLM_PROVIDER` setting (`google` default → `gemini-2.5-flash`),
  with Claude and local/OpenAI-compatible as documented drop-ins.
- `GET /conversations`, `GET /conversations/{id}` (with message history), `DELETE /conversations/{id}`
  work, all per-user scoped.
- All tests pass against `notes_rag_test` (fakes for the LLM + embeddings; `MemorySaver` for graph
  tests; one real-Postgres-checkpointer integration test); `ruff` + `mypy` clean.
- `docs/learning/03-agentic-rag.md` explains LangGraph, the graph, checkpointers, SSE, the LLM factory.

---

## 2. Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Graph pattern | **Combined agentic + corrective** (tool-calling agent with a grade → rewrite self-correction loop) | Agentic handles multi-part/vague questions; corrective grading is the anti-hallucination guard. One graph does both. |
| Weak-LLM fallback | **`AGENTIC_RETRIEVAL` flag** (default `True`); when off, always-retrieve linear path | Tool-call *decision* quality is the most model-sensitive piece (esp. local LLMs). The flag drops to a deterministic retrieve→grade→rewrite→generate path without changing the tools or graph code. |
| Framework | **LangGraph** (`StateGraph`) | Roadmap choice; explicit nodes/edges/state, native streaming + checkpointing. |
| LLM "port" | **Factory `build_chat_model(settings) → BaseChatModel`** (NOT a hand-rolled ABC) | LangGraph's tool-calling + streaming integrate with LangChain `BaseChatModel`. A custom ABC would force re-implementing `bind_tools`/streaming. A config-switched factory gives the same swappability without that cost. |
| Default LLM | **`gemini-2.5-flash`** via `langchain-google-genai`, same `GOOGLE_API_KEY` | Fast, cheap, one provider/secret already wired. |
| Provider switching | `LLM_PROVIDER` ∈ `google` (now) · `anthropic` · `openai_compatible` (local: Ollama / vLLM / LM Studio) | User wants a possible later move to a **local LLM** — `openai_compatible` + `LLM_BASE_URL` covers Ollama/vLLM with zero graph changes. Non-default provider packages are add-when-switching; the factory raises a clear error if missing. |
| Answer delivery | **Server-Sent Events (SSE)** token streaming | Chat-like UX Phase 4's frontend will consume; LangGraph streams the generate node's tokens. |
| Conversation memory | **LangGraph `AsyncPostgresSaver`** (state per `thread_id`) **+ a `conversations` table** | Checkpointer persists graph state (incl. messages) for multi-turn; the small table adds listing/titling the checkpointer doesn't provide. |
| Message storage | **No separate `messages` table** — history is read from the checkpointer's state | Avoids duplicating message storage; one source of truth. |
| `thread_id` | **= `conversations.id`** | One conversation ↔ one checkpointer thread. |
| Retrieval | **Reuse `RetrievalService`** behind the `retrieve_notes` tool | No retrieval re-implementation; the tool is a thin LangGraph wrapper. |
| Tools | **3 tools, all user-scoped:** `retrieve_notes(query, course?, tags?)`, `list_documents(course?, tags?)`, `get_document_content(document_id)` | `retrieve_notes` = Q&A + topic summaries; `list_documents` = "what do I have?" + resolve a named doc; `get_document_content` = whole-document summaries. Minimal set that covers the goal. |
| Tool filters | LLM fills `course`/`tags` when the question implies them ("in my Algorithms course…") | Optional args, never widen scope beyond the caller's own rows. |
| Summarisation | **Topic summary** = `retrieve_notes` + a summary-style answer (no new path); **whole-document summary** = `list_documents` → `get_document_content` → summarise all chunks **in `chunk_index` order** | Similarity search (top-k) is wrong for whole-document summaries — they need *all* chunks of one doc, ordered. Single-pass summarisation (flash's large context); map-reduce for very large docs deferred. |
| Corrective scope | Grade → rewrite loop wraps **`retrieve_notes` only**; `list_documents`/`get_document_content` results route **straight back to the agent → generate** | "Relevance grading" is meaningless for a deliberately-fetched named document; only similarity search can return junk worth grading. |
| Retry cap | **`max_grade_retries = 2`** rewrites, then answer honestly | Bounds cost/latency; prevents infinite grade↔rewrite loops. |
| Grounding | System prompt: answer **only** from provided context; cite page/section; refuse if insufficient | Anti-hallucination contract for lecture notes. |
| Checkpointer schema | `AsyncPostgresSaver.setup()` at startup (idempotent), **not Alembic** | LangGraph-managed tables; its own migration mechanism. Uses **psycopg3**, a second driver alongside asyncpg. |

---

## 3. Architecture

Follows the established layered + hexagonal style (Phase 0 spec §3a/§3b). New code lives in
`app/rag/graph/` (the LangGraph graph), `app/rag/llm.py` (the LLM factory), `app/services/chat.py`
(orchestration), and `app/api/chat.py` + `app/api/conversations.py` (boundary).

```
POST /chat (question, conversation_id?, course?, tags?, top_k?)   ── Boundary (api/chat.py)
        │  validate; resolve/create conversation (ownership)
        ▼
ChatService.stream_answer(user_id, conversation_id, question, filters)  ── Control (services/chat.py)
   │   - ensure conversation row exists (create + title on first turn)
   │   - invoke compiled graph with config={thread_id, user_id, filters}
   │   - translate LangGraph stream → SSE events; update conversation.updated_at
   ▼
LangGraph RAG graph (compiled once at startup, app.state)         ── app/rag/graph/
   START → [agent] ──(no tool call: ready to answer)─────────────────────► [generate] → END
             │ (tool call)
             ▼
          [tools] ── dispatch by tool ──────────────────────────────────────────────┐
             ├─ retrieve_notes ─► RetrievalService.search(user_id, q, k, filters)     │  [reuses Phase 2]
             │                       ▼                                                │
             │                    [grade] ──(relevant)──────────────► back to [agent] │
             │                       │ (weak & retries left)                          │
             │                       ▼                                                │
             │                    [rewrite] ─► back to [agent]  (≤ max_grade_retries) │
             ├─ list_documents ─────► DocumentRepository.list_for_user(...) ──► back to [agent]
             └─ get_document_content ► ChunkRepository.get_for_document(...) ──► back to [agent]

   [generate]: grounded answer + citations, streamed (answer only from context; honest refusal if weak)

checkpointer: AsyncPostgresSaver  (persists graph state per thread_id = conversation id)
AGENTIC_RETRIEVAL=false → linear path: retrieve_notes → grade → rewrite? → generate (skips the agent's tool decision)
```

**The LLM "port" (factory):** `app/rag/llm.py::build_chat_model(settings) -> BaseChatModel` switches
on `settings.llm_provider`:

| `LLM_PROVIDER` | Adapter | Package | Notes |
|----------------|---------|---------|-------|
| `google` (default) | `ChatGoogleGenerativeAI(model="gemini-2.5-flash")` | `langchain-google-genai` | Same `GOOGLE_API_KEY`. |
| `anthropic` | `ChatAnthropic(...)` | `langchain-anthropic` | Needs `ANTHROPIC_API_KEY`; add-when-switching. |
| `openai_compatible` | `ChatOpenAI(base_url=LLM_BASE_URL, ...)` | `langchain-openai` | Local via Ollama/vLLM/LM Studio; add-when-switching. |

Unknown provider → clear `ValueError`. This is the swap seam; the graph never names a provider.

**Graph state** (`app/rag/graph/state.py`, a `TypedDict`):

| Field | Type | Purpose |
|-------|------|---------|
| `messages` | `Annotated[list, add_messages]` | conversation messages (LangGraph reducer) |
| `question` | `str` | current (possibly rewritten) query |
| `context` | `list[ChunkMatch]` | chunks from the latest retrieval |
| `citations` | `list[Citation]` | sources backing the final answer |
| `retry_count` | `int` | rewrites so far (capped at `max_grade_retries`) |
| `filters` | `dict` | `course`/`tags` passed to retrieval |

**Tools** (`app/rag/graph/tools.py`) — all bound to the agent via `.bind_tools`, all user-scoped:

| Tool | Args | Backed by | Result |
|------|------|-----------|--------|
| `retrieve_notes` | `query`, `course?`, `tags?` | `RetrievalService.search` (Phase 2) | top-k similar chunks → `context` (graded) |
| `list_documents` | `course?`, `tags?` | `DocumentRepository.list_for_user` | the user's documents (id, title, filename, course) |
| `get_document_content` | `document_id` | `ChunkRepository.get_for_document` (**new**: all chunks for one doc, ordered by `chunk_index`, ownership-checked) | full document text (for whole-doc summary) |

**Per-request context (user scoping):** the graph is compiled **once** at startup. `user_id` and
`filters` flow per request via `config={"configurable": {...}}`. Each tool resolves its
repositories/`RetrievalService` from the app's **`async_sessionmaker`** (a short-lived session of its
own, not the request's session) so all access is correctly user-scoped and concurrency-safe. Queries
are embedded with the existing `EmbeddingsProvider` (`RETRIEVAL_QUERY`).

**Nodes** (`app/rag/graph/nodes.py`):
- **agent** — LLM bound with the 3 tools (`.bind_tools`). Emits a tool call, or answers directly
  (e.g. "summarise our chat" needs no retrieval).
- **tools** — executes the requested tool(s); writes results into state (`context` for `retrieve_notes`).
- **grade** — LLM structured verdict on `retrieve_notes` results: relevant to the question? (`relevant: bool`).
- **rewrite** — LLM rewrites `question` for better retrieval; increments `retry_count`; loops to agent.
- **generate** — LLM writes the final answer/summary **grounded only in the gathered context**, with
  citations; this is the node whose tokens are streamed to the client. If retries are exhausted with
  weak context, the prompt instructs an honest "I couldn't find this in your notes."

**Conditional edges:** agent→(tool call? tools : generate);
tools→(was `retrieve_notes`? grade : agent);
grade→(relevant? agent : (retries left? rewrite : generate));
rewrite→agent. When `AGENTIC_RETRIEVAL=false`, START→retrieve_notes directly (linear path), skipping
the agent's tool-call decision while keeping grade → rewrite → generate.

**Validation placement (per project conventions):** handler validates question non-empty + parses
filters + checks conversation ownership (→ `400`/`404`); `ChatService` orchestrates the graph and
conversation lifecycle; repositories stay CRUD-only; the graph/nodes hold the RAG reasoning.

---

## 4. Data model

One Alembic migration adds **`conversations`** only. The checkpointer's own tables
(`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) are created by `AsyncPostgresSaver.setup()`
at startup, **not** by Alembic.

### `conversations` — one row per chat thread (the unit users list/resume)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | **also the LangGraph `thread_id`** |
| `user_id` | UUID FK→users (CASCADE), indexed | per-user scoping |
| `title` | str, nullable | from the first question (truncated) on turn 1; optional LLM titling later |
| `created_at` | timestamptz | UTC |
| `updated_at` | timestamptz | UTC; bumped each turn (drives "recent" ordering) |

Message history is **not** stored here — it lives in the checkpointer state and is read back via
`graph.aget_state(config).values["messages"]`.

---

## 5. API & schemas

All endpoints require a valid access token (`get_current_user`, Phase 1) and only ever touch the
caller's rows/threads.

| Endpoint | Request | Success | Errors |
|----------|---------|---------|--------|
| `POST /chat` | `{ question, conversation_id?, course?, tags?, top_k? }` | `200` **`text/event-stream`** (SSE) | `400` empty question, `404` conversation not yours/missing |
| `GET /conversations` | — | `200` `list[ConversationResponse]` (newest first) | — |
| `GET /conversations/{id}` | path id | `200` `ConversationDetail` (with message history) | `404` not yours/missing |
| `DELETE /conversations/{id}` | path id | `204` (row + checkpointer thread state) | `404` not yours/missing |

**SSE event stream for `POST /chat`** (each `data:` is JSON):
- `event: meta` — `{ conversation_id }` (sent first; lets a new chat learn its id immediately)
- `event: token` — `{ delta }` (repeated; the streamed answer)
- `event: citations` — `[ Citation, ... ]` (the sources actually backing the answer)
- `event: done` — `{ }` (stream complete)
- `event: error` — `{ detail }` (on failure mid-stream)

**Schemas (Pydantic):**
- `ChatRequest`: `question: str` (non-empty), `conversation_id?: UUID`, `course?: str`,
  `tags?: list[str]`, `top_k?: int` (bounded 1–20, default from `retrieval_top_k`).
- `Citation`: `chunk_id, document_id, filename, title, page_number, section, score`.
- `ConversationResponse`: `id, title, created_at, updated_at`.
- `ConversationDetail`: `ConversationResponse` + `messages: list[MessageResponse]`.
- `MessageResponse`: `role` (`user`/`assistant`), `content`, `citations?`, derived from checkpointer state.

**Ownership:** list/detail/delete/chat filter by `user_id = current_user.id`; user-isolation is tested.
**`/search` (Phase 2) stays** as a debug surface; the graph reuses the same `RetrievalService`.

---

## 6. Configuration

New `Settings` fields (all defaulted so existing setups keep working):

| Setting | Default | Purpose |
|---------|---------|---------|
| `llm_provider` | `google` | `google` · `anthropic` · `openai_compatible` |
| `llm_model` | `gemini-2.5-flash` | chat model id |
| `llm_temperature` | `0.2` | low → grounded, less drift |
| `llm_base_url` | `None` | for `openai_compatible` (local Ollama/vLLM endpoint) |
| `anthropic_api_key` | `None` | for `anthropic` provider |
| `agentic_retrieval` | `True` | `False` → deterministic always-retrieve path (weak/local LLMs) |
| `max_grade_retries` | `2` | corrective rewrite cap |
| `chat_history_limit` | `20` | max prior messages fed to the graph (context-window guard) |
| `checkpointer_db_url` | derived | **psycopg3** URL (`postgresql://…`) from existing DB settings (asyncpg URL is `postgresql+asyncpg://…`) |

`google_api_key`, `retrieval_top_k`, and the DB connection settings already exist.
**New Python deps:** `langgraph`, `langgraph-checkpoint-postgres`, `langchain-core`,
`langchain-google-genai`, `psycopg[binary]`, `psycopg-pool`.
*(Add-when-switching, documented, not installed now: `langchain-anthropic`, `langchain-openai`.)*
**No new system deps.**

---

## 7. Application wiring (lifespan)

- On **startup**: open an `AsyncConnectionPool` (psycopg3) → build `AsyncPostgresSaver`, call
  `await saver.setup()` (idempotent), `build_chat_model(settings)`, compile the RAG graph with the
  checkpointer, and store the compiled graph + pool on `app.state`.
- On **shutdown**: close the pool.
- `app/api/deps.py` gains `get_chat_graph` (from `app.state`) and `get_chat_service`
  (graph + `conversations` repo + `async_sessionmaker` for the retrieval tool).

---

## 8. Testing (TDD, against `notes_rag_test`)

- **Unit — LLM factory:** each `LLM_PROVIDER` builds the right `BaseChatModel` type (patch the chat
  classes); unknown provider → `ValueError`; `openai_compatible` requires `llm_base_url`.
- **Unit — graph nodes** with a deterministic **`GenericFakeChatModel`/`FakeMessagesListChatModel`**
  (LangChain) + `MemorySaver`:
  - agent calls a tool when the question needs notes; answers directly otherwise.
  - grade returns relevant/irrelevant correctly; grade runs only after `retrieve_notes` (not after
    `list_documents`/`get_document_content`).
  - **rewrite loop is bounded** — with always-irrelevant grades, it rewrites at most
    `max_grade_retries` times then generates (no infinite loop).
  - generate produces an answer + citations grounded in `context`; with empty/weak context it emits
    the "couldn't find it in your notes" refusal.
  - **whole-doc summary path:** a `get_document_content` tool call routes straight back to the agent
    (no grade) and yields a summary over the full ordered chunks.
  - **`AGENTIC_RETRIEVAL=false`** takes the linear path (retrieve→grade→…→generate) without the agent
    tool-call step.
- **Unit — tools:** `retrieve_notes` calls `RetrievalService` with `user_id` + filters;
  `list_documents` returns only the caller's docs; `get_document_content` returns one doc's chunks in
  `chunk_index` order and **404s/empties on a doc the user doesn't own** (fake embeddings).
- **Service — `ChatService`:** creates a conversation + title on the first turn, reuses on later turns;
  bumps `updated_at`; user-scoping (posting to another user's conversation → `404`); history read;
  delete removes row (+ checkpointer thread).
- **Integration (API):** `POST /chat` streams `meta` → `token`s → `citations` → `done`; conversation
  persisted; **follow-up turn has prior context** (real `AsyncPostgresSaver` against `notes_rag_test`);
  `GET /conversations` lists; detail returns history; `DELETE` → `204`; `401` without token;
  **user-isolation**.
- **CI:** no Google/Anthropic key or network needed — fakes for LLM + embeddings; the Postgres
  checkpointer test uses the CI Postgres service.

---

## 9. Out of scope (deferred)

Frontend/chat UI (Phase 4); reranking, HyDE, multi-query fusion, small-to-big/contextual retrieval
(possible later retrieval-quality pass); tools beyond the three (web search, calculators, code
execution / self-built tools); **map-reduce summarisation** for very large documents (single-pass
now, relying on the model's large context); LLM **metadata backfill** for documents (the Phase 2
nullable-metadata hook — a clean Phase-3 fast-follow, not in this slice); conversation rename/share/export; LLM-generated conversation titles
(start with truncated first question); per-message persistence in our own table (checkpointer is the
source of truth); streaming of intermediate node thoughts (only the final answer streams).

---

## 10. Risks / notes

- **Local-LLM tool-calling quality (user's stated future direction):** the agentic+corrective loop
  leans on reliable tool/function calling. Many small local models do this poorly. Mitigation: the
  factory makes the swap config-only, and a weak model can fall back to the linear path; documented in
  the learning doc. The *seam* is what Phase 3 guarantees, not local-model quality.
- **Two DB drivers:** the app uses **asyncpg** (SQLAlchemy) while the checkpointer uses **psycopg3**.
  Both hit the same Postgres; we maintain a separate psycopg pool for the saver. Acceptable and
  documented; the alternative (porting everything to one driver) is out of scope.
- **Checkpointer schema outside Alembic:** `AsyncPostgresSaver.setup()` owns its tables. Run at
  startup (idempotent). Noted so the schema source isn't surprising.
- **Streaming the right tokens:** grade/rewrite also call the LLM; only the **generate** node's tokens
  must reach the client. Filter LangGraph's stream by node/tag (`stream_mode="messages"` + metadata).
- **Cost/latency:** a hard question can cost up to ~`2 + 2·max_grade_retries` LLM calls (agent, grade,
  rewrite, generate). The retry cap + `gemini-2.5-flash` keep this bounded and cheap.
- **Context-window guard:** long conversations are trimmed to `chat_history_limit` messages before the
  graph runs, so token usage stays bounded.
- **Whole-document summary size:** `get_document_content` loads *all* of a document's chunks into one
  prompt. Fine for typical lecture notes given `gemini-2.5-flash`'s large context; a very large doc
  could exceed it. Single-pass now; map-reduce summarisation is the deferred fix (§9).
- **Deleting checkpointer state:** delete the `conversations` row and the thread's checkpointer state
  (`adelete_thread` where available); if unavailable in the installed version, the row is the
  authoritative handle and orphaned checkpoint rows are harmless (documented).
- **Grounding is prompt-enforced, not guaranteed:** the corrective grade + "answer only from context"
  prompt strongly reduce hallucination but can't fully prevent it; citations let the user verify.
- **API key in CI:** none needed — all model/embedding calls are faked.

---

## 11. Appendix — Future capabilities (NOT in Phase 3)

Captured from a study-app brainstorm so the ideas aren't lost. **Phase 3 stays scoped to the 3-tool
RAG core**; everything below is a later phase or fast-follow.

**Guiding principle:** a new *tool* is justified only when there is a distinct **action / data access /
computation** the LLM can't do just by talking over retrieved context. Many "features" are merely
*answer styles* the `generate` node already handles — no tool, no code.

### Already free with the Phase-3 tools (no new tool)

Explain-simply / ELI5 / analogy · compare two concepts · in-chat examples & practice problems ·
study guide / outline / cheat-sheet for a topic or document · glossary / key-terms extraction. All of
these are `retrieve_notes`/`get_document_content` + a `generate` answer style.

### Real new tools / subsystems (each adds a data model + persistence)

| Capability | Needs | Suggested home |
|------------|-------|----------------|
| **Flashcards + spaced repetition** ⭐ | `flashcards` + `reviews` tables; SM-2/Anki scheduling; tools `create_flashcards`, `get_due_cards`, `record_review` | **Dedicated "Active Recall" phase** |
| **Quiz me / answer grading** | Q-generation is free; *grading + scoring* needs `quiz_attempts` + `record_answer` | Same phase as flashcards |
| **Study plan / progress tracking** | `study_plans`, `topic_progress` tables + goal/mastery tools | Later phase |
| **Auto-tagging / metadata backfill** | service/tool writing `title`/`course`/`tags` on documents (the Phase-2 nullable-metadata hook) | **Phase-3 fast-follow (small)** |
| **Find related notes / "what to review next"** | `find_related(concept)` retrieval variant | Small optional add |
| **Web-augmented mode** | external search API; **separate, clearly-labelled mode** — breaks the "only from your notes" grounding contract | Deliberate later decision |

### Tentative roadmap beyond Phase 3

1. Auto-tagging / metadata backfill (small, reuses the LLM).
2. **"Active Recall" phase** — flashcards + quizzes + spaced repetition + progress (the main study
   differentiator).
3. Study planner; web-augmented mode.
