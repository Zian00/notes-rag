# Phase 4 — React Frontend: Concepts

This guide explains the frontend we built and *why* each piece works the way it does.
It's accurate to the code in `frontend/src/` and `frontend/tests/`.

---

## The big picture

Phases 0–3 built a fully-working API: auth, document ingestion, and a streaming agentic RAG
chat endpoint. Phase 4 is the SPA that talks to that API — nothing more. It's a Vite + React 19
+ TypeScript app in `frontend/`, and it never talks to Postgres or LangGraph directly; every
piece of state it owns is either a UI-only concern (is the drawer open, what's typed in the
box) or a cache of a response from `backend/app`.

The provider tree in `frontend/src/App.tsx` is deliberately ordered:

```
ThemeProvider (next-themes)
  └─ QueryClientProvider (TanStack Query)
       └─ AuthProvider (our AuthContext)
            └─ BrowserRouter
                 └─ AppRoutes
```

**Why this order?** Theme has no data dependency on anything else, so it wraps everything.
`AuthProvider` needs `QueryClientProvider` above it because `logout()` calls
`queryClient.clear()` — it must be able to reach a `QueryClient` via `useQueryClient()`.
`AppRoutes` (and everything under it — `ProtectedRoute`, pages) needs `useAuth()`, so
`AuthProvider` sits above `BrowserRouter`.

---

## Dev networking: the Vite proxy

`frontend/vite.config.ts` proxies `/api/*` to the backend:

```ts
server: {
  proxy: {
    "/api": {
      target: "http://localhost:8000",
      changeOrigin: true,
      rewrite: (p) => p.replace(/^\/api/, ""),
    },
  },
},
```

**Why proxy instead of pointing the client straight at `localhost:8000`?** Same-origin. If the
browser thinks it's talking to `localhost:5173` (Vite's own dev server) the whole time, the
httpOnly refresh cookie (see below) is a same-site cookie with no CORS configuration needed at
all — the browser just sends it automatically on every `/api/*` request, and there is no
preflight to configure. Point the client directly at port 8000 instead and you're suddenly
doing cross-origin cookie auth, which means `SameSite`/`CORS`/`Secure` all have to be revisited
(see "Deferred" below — this is exactly the split-origin problem punted to Phase 5).

`frontend/src/api/client.ts` resolves the API base as:

```ts
const RAW_BASE = import.meta.env.VITE_API_BASE ?? "/api"
const BASE = new URL(RAW_BASE, window.location.origin).toString().replace(/\/$/, "")
```

No `.env` file exists, so `VITE_API_BASE` is `undefined` at runtime and `RAW_BASE` falls back to
`"/api"` — the proxy path. **Why wrap a bare relative string in `new URL(...)`?**
`openapi-fetch` (and the hand-rolled `fetch` calls in `chatStream.ts`) build requests with the
platform's `Request`/`URL` constructors, which — unlike a browser resolving a relative `<a
href>` — require an *absolute* URL. A bare `"/api"` throws `Invalid URL` under Node's `fetch`
(which is what Vitest/jsdom actually run on, since there's no browser location bar to resolve
against implicitly). Resolving `"/api"` against `window.location.origin` gives an absolute URL
that still points at the same relative path — so it works identically in the real browser (proxy
active) and in tests (MSW intercepts by full URL).

---

## Auth model

### Where the tokens live

| Token | Storage | Why |
|---|---|---|
| Access | JS variable in `frontend/src/api/client.ts` (`let accessToken`) | In-memory only — never `localStorage`/`sessionStorage`. An XSS payload that runs arbitrary JS in the page can always read those; it *cannot* read a variable closed over inside a module unless it can also call exported functions, and none of this module's exports leak the raw value. |
| Refresh | httpOnly cookie, set by the backend | JS never touches it at all — `document.cookie` can't see it, so XSS can't exfiltrate it. |

The tradeoff: the access token is gone on every full page reload (it's just a variable), so the
app needs a way to silently get a new one on load — see below.

### Silent refresh on mount

`frontend/src/auth/AuthContext.tsx`'s `AuthProvider` runs one effect on mount that calls
`refreshAccessToken()` (POST `/auth/refresh`, cookie sent automatically via
`credentials: "include"`) and, if that succeeds, `GET /auth/me` to populate the user. Either
failure path sets `status = "anon"`; success sets `status = "authed"`.

**Why a `ref` guard, not just an empty dependency array?** React 18/19 StrictMode
double-invokes effects in dev (mount → cleanup → remount) to surface missing-cleanup bugs. An
empty-deps effect still runs twice under StrictMode. `hasAttemptedSilentRefresh` is a `ref` (not
state, so setting it doesn't trigger a re-render) that survives the double-invoke and makes sure
the refresh call only actually fires once per real app load.

`frontend/src/routes/ProtectedRoute.tsx` renders a full-page spinner while `status === "loading"`
— without that, an anon-by-default first render would flash the login page for a moment even
for a user who has a valid refresh cookie.

### The 401 → refresh → retry middleware

Every typed API call goes through `openapi-fetch`'s middleware hook in `client.ts`:

- `onRequest`: attaches `Authorization: Bearer <token>` if we have one.
- `onResponse`: if the response is a 401 **and** the request wasn't itself to `/auth/*`, call
  `refreshAccessToken()` and, on success, replay the original request once with the new token.

**The anti-loop guard.** Checking `request.url.includes("/auth/")` matters because without it,
a failed `/auth/refresh` call (itself a 401) would trigger *another* refresh attempt, which
could fail the same way, forever. Bad credentials on `/auth/login` would similarly loop.
Skipping refresh for any `/auth/*` URL breaks the cycle — those endpoints are expected to
sometimes return 401 as a normal, final outcome.

**The body-buffering trick.** By the time `onResponse` runs, `openapi-fetch` has already called
`fetch(request)`, which *consumes* that request's body stream. A `POST`/`PUT`/`PATCH` retry
needs the same body again, but you can't read a spent stream twice. The fix: `onRequest` calls
`request.clone().arrayBuffer()` *before* the body is ever read, and stashes the bytes in a
`Map<id, ArrayBuffer>` keyed by the middleware's per-request id. `onResponse` looks up that
buffer to build the retry `Request`, then deletes the entry (whether or not it retries) so the
map doesn't leak memory across the app's lifetime.

**`AuthError` carries the HTTP status.** `AuthContext.login`/`register` throw an `AuthError`
(`frontend/src/api/authError.ts`) with the response's status code attached, rather than a plain
`Error`. That lets `LoginPage`/`RegisterPage` show *different* messages for 401 (bad
credentials) vs. 409 (email already registered) vs. 422 (validation failure) without
re-inspecting the response themselves.

---

## Typed API layer

`frontend/package.json`'s `gen:api` script runs `openapi-typescript ./openapi.json -o
src/api/schema.ts` — it reads a snapshot of the backend's OpenAPI schema and generates
TypeScript types for every path, method, request body, and response shape into
`frontend/src/api/schema.ts`. Nothing in that file is hand-written; if the backend's Pydantic
models change, regenerating this file is how the frontend's types catch up (and the compiler
will flag every call site that's now wrong).

On top of the generated types, `client.ts` builds two clients:

- `fetchClient = createFetchClient<paths>(...)` (`openapi-fetch`) — a thin typed wrapper around
  `fetch`; `fetchClient.GET("/documents", ...)` is fully typed against `schema.ts`.
- `$api = createReactQueryClient(fetchClient)` (`openapi-react-query`) — wraps `fetchClient` in
  TanStack Query hooks, so `$api.useQuery("get", "/documents")` gets caching, loading/error
  states, and refetch-on-window-focus for free, still fully typed.

`frontend/src/api/hooks/useDocuments.ts` and `useConversations.ts` are thin wrappers over `$api`
that give call sites a normal-looking hook (`useDocuments(course)`) instead of exposing the
`(method, path, options)` tuple shape everywhere.

**The empty-object partial-match invalidation trick.** `getDocumentsListKey()` calls
`$api.queryOptions("get", "/documents", { params: { query: {} } }).queryKey` — deliberately with
an *empty* query object, not `{ course: undefined }`. TanStack Query's partial-match
invalidation walks the *filter's own keys*; an empty object has no keys to check, so it matches
every cached `/documents` query regardless of which `course` filter it was fetched with. This
means uploading or deleting a document invalidates the documents list no matter which
course-filtered view is currently on screen, with one `invalidateQueries` call instead of one
per possible filter value.

---

## Streaming chat (the interesting part)

### Why POST + `fetch`, not `EventSource`

The browser's native `EventSource` API only supports `GET` requests with no body. A grounded
chat answer needs a request *body* — the question text, an optional `conversation_id`, and
retrieval filters (`course`, `tags`, `top_k`) — so `EventSource` is a non-starter.
`frontend/src/api/chatStream.ts` instead does a normal `fetch("/chat", { method: "POST", body:
... })` and reads `res.body` (a `ReadableStream<Uint8Array>`) by hand.

### The SSE frame format and the buffer-until-blank-line parser

The backend (Phase 3's `ChatService.stream_answer`) emits frames like:

```
event: token
data: {"delta":"Hello "}

```

— an `event:` line, a `data:` line, then a blank line (`\n\n`) as the frame terminator.
`parseSseStream` in `chatStream.ts` accumulates bytes into a string `buffer` and repeatedly
looks for `\n\n`:

```ts
while ((separatorIndex = buffer.indexOf(FRAME_SEPARATOR)) !== -1) {
  const rawFrame = buffer.slice(0, separatorIndex)
  buffer = buffer.slice(separatorIndex + FRAME_SEPARATOR.length)
  const frame = parseFrame(rawFrame)
  if (frame) yield frame
}
```

**Why buffer instead of parsing each chunk as it arrives?** TCP/HTTP chunk boundaries have
nothing to do with SSE frame boundaries — a single `reader.read()` might deliver half a frame,
several frames, or even split the two-byte `\n\n` separator itself across two reads. The tests
in `frontend/tests/chatStream.test.ts` exercise all three cases directly (a `data:` line split
mid-JSON, two frames in one chunk, and the separator itself split across chunks) — the buffer
approach handles all of them because it never assumes a chunk aligns with a frame.

`TextDecoder`'s `{ stream: true }` option is used for the same reason at the byte level: it
keeps a partial multi-byte UTF-8 sequence internally buffered across `decode()` calls instead of
emitting a replacement character (`�`) for a multi-byte character split across chunk boundaries.

`parseFrame` is intentionally narrow — it expects exactly one `event:` line and one `data:` line
per frame (matching the backend's exact emitter format) and *skips* (doesn't throw on) anything
that doesn't parse, so one malformed frame (or a stray SSE comment/keep-alive line) can't take
down an otherwise-good stream.

### `reader.cancel()` on early exit

The parser's `finally` block calls `await reader.cancel().catch(() => {})`, not just
`reader.releaseLock()`. `cancel()` tears down the underlying network connection; if the consumer
(the `useChat` hook, on unmount or `stop()`) stops reading before the server sends `done`,
skipping `cancel()` would leave the HTTP connection open, still receiving bytes nobody reads.
`frontend/tests/chatStream.test.ts`'s "cancels the underlying stream" test builds a stream that
never self-closes specifically to prove `cancel()` — not just lock release — actually happens.

### The `useChat` state machine

`frontend/src/api/hooks/useChat.ts` owns the *live* (in-progress) message list for one chat
session. On `send(question, filters)`:

1. Appends a user message and an empty assistant placeholder to `messages`, flips
   `isStreaming = true`.
2. Iterates `streamChat(...)`'s async generator frame-by-frame:
   - `meta` → records the (possibly brand-new) `conversation_id`; if this was a *new* chat
     (no id when `send()` was called), calls `onConversationCreated(id)` exactly once — `
     frontend/src/routes/ChatPage.tsx` wires this to `navigate("/chat/:id", { replace: true })`
     so the URL reflects the real conversation without leaving the bare `/chat` entry in
     browser history.
   - `token` → appends `delta` onto the assistant placeholder's `content` (this is what makes
     the answer appear to type itself).
   - `citations` → patches the assistant message with the citation list, rendered later by
     `frontend/src/components/chat/Citations.tsx`.
   - `error` / `done` → breaks out of the loop.
3. In `finally`, flips `isStreaming = false` and invalidates the conversations list query key —
   a new chat needs to appear in the sidebar, and an existing one just moved to the top
   (`updated_at` bumped), either way the sidebar's cached list is now stale.

**Abort is not an error.** The `catch` block checks `controller.signal.aborted ||
(err instanceof DOMException && err.name === "AbortError")` before marking the assistant turn as
errored. Calling `stop()` (or unmounting mid-stream) aborts the `fetch` on purpose — that's a
lifecycle event, not a failure, so whatever content already streamed in stays on screen as-is
rather than getting overwritten with an error message.

**The history/live seed rule.** `useChat` deliberately does *not* fetch persisted history itself
— that's `useConversation(conversationId)` in `frontend/src/api/hooks/useConversations.ts`,
called from `ChatPage`. `ChatPage` hands that fetched history to `useChat`'s `seed(history)`
function, which only replaces `messages` **when the live list is currently empty**:

```ts
const seed = useCallback((history: ChatMessage[]) => {
  setMessages((prev) => (prev.length === 0 ? history : prev))
}, [])
```

**Why "seed only when empty," not "always seed on data arrival"?** Consider a brand-new chat:
`send()` immediately populates `messages` with the just-typed question and streaming answer.
Shortly after, the `meta` frame's navigation to `/chat/:new-id` mounts `useConversation` for
that same id, which — once it resolves — would otherwise try to seed history for a conversation
that (from the DB's point of view, depending on timing) might not even reflect the latest turn
yet. Since `messages` is already non-empty at that point, `seed` is a no-op, and the live,
just-streamed turns are what stays on screen. Opening an *existing* conversation (or reloading
`/chat/:id`) is the opposite case: `messages` starts empty, so the fetched history seeds it
correctly.

---

## Component layout

- **`frontend/src/components/layout/AppShell.tsx`** — the two-pane shell for every authed
  route: a fixed `<aside>` sidebar at `md:` breakpoint and up, collapsing into a header +
  slide-in drawer (with Esc-to-close, wired only while the drawer is open) below it. Rendered
  once `ProtectedRoute` confirms `status === "authed"`, so everything under it can assume a
  signed-in user exists.
- **`frontend/src/routes/ProtectedRoute.tsx`** — guards `/chat*` and `/documents`: shows a
  spinner while `status === "loading"` (silent refresh in flight), redirects to `/login` when
  `"anon"` (preserving the intended destination in router state), otherwise renders `<Outlet
  />`.
- **`frontend/src/components/layout/Sidebar.tsx`** — nav links, the conversation list
  (`useConversations`), a "New chat" button, and logout. "New chat" stamps a fresh UUID into
  `location.state.newChatNonce` on every click rather than relying on the route changing —
  `ChatPage` watches that nonce (not just the URL param) so clicking "New chat" while already on
  bare `/chat` still resets the live thread, which a plain route-param comparison would miss
  since the pathname didn't change.
- **`frontend/src/components/documents/*`** — `UploadDropzone`, `DocumentList`/`DocumentRow`,
  and `MetadataFields` for the documents page (upload/list/delete flow, built in an earlier
  milestone of this same phase).
- **`frontend/src/components/chat/*`** — `MessageList` (auto-scrolls to bottom on every message
  change via `scrollIntoView`, guarded because jsdom doesn't implement that method at all),
  `MessageBubble` (renders one message; shows an animated cursor only on the single message
  that's actively streaming), `Citations` (a collapsible "Sources" list — deduped by document
  server-side, so it doesn't try to line citation numbers up with the answer's inline `[n]`
  markers), and `ChatInput` (textarea with Enter-to-send / Shift+Enter-for-newline, an
  IME-composition guard so confirming Japanese/Chinese input via Enter doesn't prematurely
  submit, and a collapsible filters row for `course`/`tags`/`top_k`).

---

## Testing approach

Stack: **Vitest** (test runner, jsdom environment) + **React Testing Library** (render/query
components the way a user would) + **MSW** (Mock Service Worker — intercepts `fetch` at the
network level so components can be tested against realistic HTTP responses without a real
backend).

**Two different mocking strategies for streaming, used deliberately for different layers:**

- `frontend/tests/chatStream.test.ts` tests the *parser itself* — it mocks `globalThis.fetch`
  directly and hand-builds `ReadableStream`s with exact byte chunking (split frames, split
  separators, non-closing streams for cancel-testing). This is the only layer that needs to
  prove the byte-level buffering logic is correct.
- `frontend/tests/useChat.test.tsx` and `frontend/tests/chat-ui.test.tsx` mock the *whole*
  `streamChat` function (`vi.mock("@/api/chatStream", () => ({ streamChat: vi.fn() }))`) to
  yield a scripted array of already-parsed `ChatFrame`s. These layers care about the state
  machine and UI reacting to frames — not about re-proving the SSE parsing, which is already
  covered.

**The MSW `server.listen()` module-scope gotcha.** `frontend/tests/setup.ts` calls
`server.listen()` at the top level of the file, not inside a `beforeAll()`:

```ts
server.listen()
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
```

**Why not `beforeAll`?** Vitest evaluates a setup file's top-level code *before* a test file's
own imports run. `frontend/src/api/client.ts` calls `createFetchClient()` at module import
time, and `openapi-fetch` captures `globalThis.fetch` once, internally, at that moment. If MSW
patched `globalThis.fetch` inside a `beforeAll` (which runs *after* the test file's imports have
already executed and `client.ts` has already captured the unpatched `fetch`), the typed client's
requests would bypass MSW entirely and hit the real network. Calling `server.listen()` at module
scope in the setup file guarantees the patch is in place before anything else imports `client.ts`.

---

## Deferred to Phase 5 / out of scope

Noted honestly rather than silently skipped:

- **No Playwright (or other) end-to-end tests.** Everything here is component/hook/unit level
  (Vitest + RTL + MSW); a real browser driving the full stack (backend + Postgres + frontend
  together) is not covered yet.
- **No production build pipeline for the frontend.** There's no Dockerfile/nginx config or CI
  job that builds and serves `frontend/dist/` — `npm run build`/`npm run preview` work locally,
  but there's no containerized or CI-verified production path yet.
- **Split-origin cookie/CORS story is untested.** The dev proxy makes same-origin cookies "just
  work" without touching `SameSite`/`CORS` config at all. A real deployment where the frontend
  and backend are on different origins (e.g. separate domains, no shared reverse proxy) would
  need that revisited — it hasn't been exercised here.
- **No rerank/HyDE in the UI.** These are retrieval-quality improvements that live entirely on
  the backend (see `docs/learning/03-agentic-rag.md`'s deferred list) — the frontend has nothing
  to do for them either way, but they gate what "better answers" would look like from this UI.
- **No per-message citation history.** Same backend limitation as Phase 3: `GET
  /conversations/{id}` returns only role/content per message, so `ChatPage`'s history-seeded
  messages never have a `citations` field — only the *live*, just-streamed turn in a session
  shows its sources. Reopening an old conversation shows the text but not which notes it cited.

---

## How to test it

```bash
cd frontend
npm install
npm run test        # Vitest, one-shot
npm run test:watch  # Vitest, watch mode
npm run lint         # ESLint
npm run typecheck   # tsc --noEmit, project references
```

No backend, Postgres, or `.env` file is needed for the test suite — MSW mocks every HTTP call.
To run the app against a live backend, see the "Frontend (local dev)" section in the root
`README.md`.
