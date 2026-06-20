# Phase 3 — Agentic RAG / LangGraph: Concepts

This guide explains the agentic RAG system we built and *why* each piece works the way it
does. It's accurate to the code in `backend/app/rag/`, `backend/app/services/chat.py`, and
`backend/app/main.py`.

---

## The big picture

Phase 2 built the *data plane* — ingestion and vector search. Phase 3 puts an LLM on top of
it. A user asks a question in plain English; the system retrieves the most relevant passages
from their own notes, grades whether those passages actually answer the question, and streams
a grounded, cited answer back token by token. Follow-up questions remember the conversation.

The core idea: **never invent an answer**. The model is told to answer *only* from the
provided context — and if the notes don't contain the answer, to say so honestly.

---

## What LangGraph is

LangGraph is a library for building stateful, multi-step LLM workflows as a directed graph.
Three concepts:

- **State** — a typed dictionary that all nodes can read from and write to. It persists across
  steps inside one turn and, via the checkpointer, across turns in the same conversation.
- **Nodes** — async functions that take the current state (plus a config) and return a *patch*
  (a dict of keys to update). Nodes do the actual work: calling the LLM, calling tools,
  grading results.
- **Edges** — connections between nodes that determine execution order. A *conditional edge*
  inspects the state after a node runs and routes to one of several next nodes (e.g. "grade
  said relevant → go to generate; grade said not relevant → go to rewrite").

This gives you a structured flow with branching and loops — things that would be messy spaghetti
code if you wrote them as plain `if/else` chains.

---

## Our state

`app/rag/graph/state.py` defines `RagState`:

| Key | Type | Purpose |
|---|---|---|
| `messages` | `list[AnyMessage]` with `add_messages` | Full conversation history; `add_messages` appends rather than replaces |
| `question` | `str` | Current query (may be rewritten by the rewrite node) |
| `context` | `list[dict]` | Chunks from the latest retrieval; used for grounding and citations |
| `relevant` | `bool` | Grade verdict from the grader |
| `retry_count` | `int` | How many rewrites have happened so far |

`add_messages` is a LangGraph reducer — instead of overwriting the list, it appends. The
checkpointer persists the accumulated `messages` list across turns keyed by `thread_id`.

---

## The combined agentic + corrective graph

We use **two patterns merged into one graph**:

- **Agentic:** the LLM holds retrieval as a *tool* and decides whether to call it (or answer
  directly from memory for greetings, meta questions, etc.).
- **Corrective:** after retrieval, a grading step checks relevance; if the results are weak, the
  query is rewritten and retrieval runs again (bounded by `max_grade_retries = 2`).

Here is the full flow:

```
START
  │
  ▼
[agent]  ── decides whether to call a tool
  │
  ├─ (tool_calls present?) ──► [tools]  ── executes the tool call
  │                                │
  │                                ├─ (retrieve_notes?) ──► [grade]
  │                                │                           │
  │                                │              (relevant?) ─┤
  │                                │                           ├─ yes ──► [generate] ──► END
  │                                │                           └─ no, retries left ──► [rewrite]
  │                                │                                          │
  │                                │                                          └──► [agent] (loops)
  │                                │
  │                                └─ (other tool, e.g. list_documents?) ──► [agent]
  │
  └─ (no tool_calls) ──► [generate] ──► END
```

**Why `grade → generate` (not back to agent) when relevant?** The agent already decided to
retrieve; the grade node confirmed the results are useful. Routing from grade straight to
generate skips a redundant agent LLM call that would just say "ok, generate now." Fewer calls
= lower latency and cost.

**Why does `get_document_content` skip grading?** Grading makes sense for similarity search
(`retrieve_notes`), which may return off-topic passages. `get_document_content` is a
deliberate whole-document fetch — the user asked for a specific document, so relevance grading
is meaningless. It routes back to the agent instead.

**Rewrite cap:** after `max_grade_retries` (default 2) rewrites, the graph generates anyway
from whatever context it has, and the model's grounding prompt forces it to admit "I couldn't
find this in your notes" rather than hallucinating.

### The `AGENTIC_RETRIEVAL=false` fallback

Some weak or local models can't reliably format tool calls. Setting `AGENTIC_RETRIEVAL=false`
replaces the agent node with a tiny `force_retrieve` node that injects a `retrieve_notes`
call unconditionally, then flows `tools → grade → rewrite/generate`. The grade → rewrite loop
still runs — the only change is that the LLM's *tool-call decision* is removed.

---

## The 3 tools — and why only 3

`app/rag/graph/tools.py` defines three tools via `build_tools(...)`:

| Tool | When the agent calls it | Why separate |
|---|---|---|
| `retrieve_notes(query, course?, tags?)` | Q&A and topic summaries | Semantic search over chunks |
| `list_documents(course?)` | "What notes do I have?" or resolving a named document | Returns document metadata, not chunk text |
| `get_document_content(document_id)` | Whole-document summaries | Fetches all chunks in `chunk_index` order; top-k similarity search is wrong here |

**Why only 3?** The LLM routes by reading each tool's description. A minimal, well-described
set is more reliable than a large set with overlapping purposes — the model is less likely to
pick the wrong tool. These 3 cover the full goal: Q&A, browsing, and whole-doc summaries.

**Security: `user_id` is never LLM-supplied.** Each tool receives a `config: RunnableConfig`
parameter that is auto-injected by LangChain (hidden from the model's schema). The tool reads
`user_id` from `config["configurable"]["user_id"]`, which `ChatService` sets from the
authenticated JWT — not from the LLM's output. The model *cannot* widen scope to another
user's data.

---

## The configurable LLM factory

`app/rag/llm.py` exports `build_chat_model(settings) -> BaseChatModel`. It selects the
provider from `settings.llm_provider`:

| Provider | Package | Config |
|---|---|---|
| `"google"` (default) | `langchain-google-genai` (hard dep) | `GOOGLE_API_KEY`, `LLM_MODEL=gemini-2.5-flash` |
| `"anthropic"` | `langchain-anthropic` (add-when-switching) | `ANTHROPIC_API_KEY` |
| `"openai_compatible"` | `langchain-openai` (add-when-switching) | `LLM_BASE_URL` (e.g. Ollama at `http://localhost:11434/v1`) |

**Why a factory returning `BaseChatModel`, not a hand-rolled ABC?**
LangGraph's tool-calling and token streaming integrate with `BaseChatModel` — specifically
`.bind_tools(...)` (makes the agent tool-aware) and `.astream(...)` (token-by-token streaming).
Re-implementing those behind a custom interface would be costly and fragile. The factory gives
the same swappability — the graph never names a provider, only a `BaseChatModel` — without
any of that overhead.

The `anthropic` and `openai_compatible` packages are *not* installed by default. The factory
raises a clear `ValueError` with install instructions if a provider is selected but its
package is missing.

---

## SSE streaming

`ChatService.stream_answer` is an async generator that yields Server-Sent Event frames in
order:

| Event | Payload | When |
|---|---|---|
| `meta` | `{"conversation_id": "..."}` | Immediately, before the graph runs |
| `token` | `{"delta": "..."}` | One per streaming chunk from the **generate node only** |
| `citations` | `[{chunk_id, document_id, filename, ...}]` | After the graph finishes, from final state |
| `done` | `{}` | After citations |
| `error` | `{"detail": "..."}` | On any unhandled exception |

Only the **generate node** tokens are streamed — tool calls, grade verdicts, and rewrite
outputs are internal plumbing the user doesn't need to see.

`ChatService` uses its **own `async_sessionmaker`** (not the request-scoped session injected
by FastAPI). FastAPI closes the request's DB session when the endpoint function returns the
`StreamingResponse` object — which happens *before* the generator body runs. Using a
request-scoped session would cause use-after-close errors mid-stream.

---

## The Postgres checkpointer

LangGraph's `AsyncPostgresSaver` persists graph state to Postgres so conversations survive
server restarts and pick up where they left off.

**`thread_id` = `conversations.id`** — each conversation row in our `conversations` table maps
1-to-1 to a checkpointer thread. The conversation row provides listing and titling (things the
checkpointer doesn't offer); the checkpointer provides the full message history.

**Two DB drivers, one database:** the app uses `asyncpg` (via SQLAlchemy `async_sessionmaker`)
for all its own queries. The checkpointer uses `psycopg3` (`psycopg[binary]`) via
`AsyncConnectionPool`, because `AsyncPostgresSaver` is a psycopg3 client. Both talk to the
same Postgres instance — just through different driver libraries.

The psycopg3 pool needs three specific settings:

```python
pool = AsyncConnectionPool(
    conninfo=settings.checkpointer_conninfo,   # strips "+asyncpg" from DATABASE_URL
    kwargs={
        "autocommit": True,           # the saver manages its own transactions
        "prepare_threshold": 0,       # disables prepared statements (don't survive reconnects)
        "row_factory": dict_row,      # saver parses rows as dicts, not tuples
    },
)
```

**Checkpointer schema is created by `setup()` at startup, not by Alembic.** `AsyncPostgresSaver.setup()`
is idempotent — it creates the `checkpoints`, `checkpoint_writes`, and `checkpoint_blobs` tables if
they don't exist. These tables are LangGraph-managed; putting them in our Alembic migrations would
couple our schema to LangGraph's internals.

**`checkpointer_conninfo` strips `+asyncpg`.** `DATABASE_URL` is `postgresql+asyncpg://...` (the
SQLAlchemy dialect suffix). psycopg3 doesn't understand that suffix and would fail to connect. The
`Settings.checkpointer_conninfo` property strips it: `"postgresql+asyncpg://..." → "postgresql://..."`.

---

## The anti-hallucination grounding contract

The generate node's system prompt (`GENERATE_SYSTEM` in `app/rag/graph/prompts.py`) is strict:

> "Answer using ONLY the provided context from the student's notes. Cite sources inline like
> [1], [2] matching the numbered context. If the context does not contain the answer, say
> clearly: 'I couldn't find this in your notes.' Do not use outside knowledge."

The retrieved chunks are rendered as a numbered list (`[1]`, `[2]`, ...) so inline citations
are unambiguous. If context is empty (e.g. the retrieval returned no results at all),
`format_chunks_for_llm` returns `"NO RESULTS."` — and the model's prompt forces it to respond
with the refusal phrase instead of inventing an answer.

---

## Limitations and deferred work

A few known gaps — noted here honestly rather than hidden:

- **History omits per-message citations.** `GET /conversations/{id}` returns role + content
  for each message. Citations (the `context` state) reflect only the *latest* turn — the
  checkpointer doesn't store per-message citation payloads. Citations from earlier turns are
  not retrievable after the fact; they were delivered live in the SSE stream.

- **Rewrite is blind.** The `rewrite` node sends only the original question to the LLM and
  asks for a better search query. It doesn't see *what* the previous retrieval returned or
  *why* it was graded irrelevant. A smarter rewrite would include the failed results as
  context — deferred.

- **No reranking, HyDE, or multi-query.** Retrieval is single-pass cosine similarity (from
  Phase 2). Techniques like HyDE (hypothetical document embeddings), cross-encoder reranking,
  or multi-query expansion would improve recall — all deferred to a later phase.

- **Whole-document summaries are single-pass.** `get_document_content` returns all chunks at
  once. For very large documents this may exceed the model's context window. A map-reduce
  approach (summarise chunks in batches, then summarise the summaries) is the fix — deferred.

- **Grading quality is model-dependent.** The grader uses `with_structured_output(Grade)`,
  which relies on the model's ability to return a structured JSON response. Strong models
  (Gemini, Claude) handle this well. Weak local models may not — in that case set
  `AGENTIC_RETRIEVAL=false` to bypass grading and always generate from whatever was retrieved.

---

## How to test it

### Automated tests (no API key needed)

```bash
make test
```

Tests use `FakeChatModel` (scripted responses, no network) and `FakeEmbeddingsProvider`
(deterministic short vectors). The graph is built with `InMemorySaver` — no Postgres needed
for graph/service tests. One test (`test_checkpointer_integration.py`) uses a real
`AsyncPostgresSaver` against the test database.

### Live end-to-end

1. Set your Google API key in `backend/.env`:
   ```
   GOOGLE_API_KEY=your_key_here
   ```

2. Start the stack:
   ```bash
   make dev
   ```

3. Open the interactive docs: `http://localhost:8000/docs`

4. Try the flow:
   - `POST /auth/register` → `POST /auth/login` → click **Authorize** with the access token
   - `POST /documents` — upload a `.txt`, `.pdf`, or `.pptx` file
   - `POST /chat` — send `{ "question": "summarise the key points" }` and watch the SSE stream
   - `GET /conversations` — confirm the conversation is listed
   - `GET /conversations/{id}` — see the message history
   - `POST /chat` with `{ "question": "...", "conversation_id": "<id from meta event>" }` — continue the conversation
