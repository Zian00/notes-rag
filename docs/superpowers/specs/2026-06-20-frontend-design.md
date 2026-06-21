# Phase 4 — Frontend (React) : Design Spec

**Date:** 2026-06-20
**Status:** Approved
**Phase:** 4 of 5 (see roadmap in `docs/superpowers/specs/2026-06-13-foundation-design.md`)

---

## 1. Context

Phases 0–3 delivered a complete backend: per-user JWT auth (access token + httpOnly refresh
cookie), document ingestion + vector search, and an agentic RAG chat API that streams grounded,
cited answers over SSE with Postgres-persisted multi-turn conversations. **There is no UI yet** —
everything has been exercised via `/docs` and tests.

Phase 4 builds the **React + Vite + TypeScript** single-page app that is the human face of the
system: register/login, upload & manage documents, and chat with streaming cited answers inside a
conversation sidebar. It consumes the existing API only — **no backend changes** are expected.

### Phase 4 goal

A logged-in user can, entirely from the browser: **register/login**, **upload** lecture files and
see/manage their documents, **ask questions** and watch a **grounded, cited answer stream in token
by token**, and **resume past conversations** from a sidebar. Running locally via `make dev`
(backend) + the Vite dev server, typed end-to-end against the backend's OpenAPI, and tested.

### Definition of done

- A user can register, log in, and stay logged in across refreshes (silent token refresh); logout works.
- Documents: drag/drop or pick a file (+ optional title/course/tags) → upload with progress → it
  appears in a list; delete works; errors (too large, unsupported, duplicate `409`) are surfaced clearly.
- Chat: send a question → assistant answer **streams token-by-token** → **citations** render under the
  answer (linking to the source document/section); follow-ups in the same conversation keep context;
  a sidebar lists conversations (newest first) and opens their history; "New chat" starts a fresh one.
- All API calls are typed from the backend's **generated OpenAPI types**; 401s trigger a silent
  refresh-and-retry, falling back to the login screen.
- `npm run lint`, `npm run typecheck`, and `npm run test` (Vitest) pass; `npm run build` produces a
  production bundle.
- `docs/learning/04-frontend.md` explains the new concepts.

---

## 2. Decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Framework / build | **React 18 + Vite + TypeScript** | Project mandate; fast dev server + HMR, first-class TS. |
| Routing | **React Router** (data router) | Standard SPA routing; protected-route pattern. |
| Styling / components | **Tailwind CSS + shadcn/ui** | Utility CSS + accessible components copied into the repo (owned, no runtime lock-in). |
| Server state | **TanStack Query** (React Query) | Caching/refetch/invalidation for documents & conversations; removes hand-rolled loading/error/caching. |
| Client/auth state | **Light React Context** (`AuthContext`) | Holds the in-memory access token + current user; small, no Redux. |
| Auth token storage | **Access token in memory; refresh token = backend httpOnly cookie** | Backend already issues an httpOnly, `secure`, `samesite` refresh cookie on `/login` and rotates it on `/auth/refresh`. JS never sees the refresh token (XSS-safe). Access token lives in memory only. |
| Session continuity | **Silent refresh** on app load + on any `401` | Call `POST /auth/refresh` (cookie sent automatically) → new access token → retry. Fail → login screen. |
| API types | **Generated from OpenAPI** (`openapi-typescript`) + **`openapi-fetch`** typed client | Single source of truth = backend `/openapi.json`; no drift; autocomplete for every endpoint. |
| Chat streaming | **`fetch` + `ReadableStream`** SSE parser (POST) | `EventSource` is GET-only; the chat endpoint is POST `text/event-stream`. Parse `event:`/`data:` frames manually. |
| Dev networking | **Vite proxy `/api` → `http://localhost:8000`** | Same-origin in dev → cookies + CORS "just work"; one base path the client targets. |
| Testing | **Vitest + React Testing Library + MSW** | Component/hook tests with mocked API (Mock Service Worker); fast, no live backend. |
| Package manager | **npm** (lockfile committed) | Ubiquitous; no extra tooling. (pnpm optional later.) |

**Notable adjustment from brainstorm:** the brainstorm picked "refresh token in localStorage", but the
backend implements the **safer httpOnly-cookie** refresh flow — so the refresh token is handled by the
browser/cookie, not JS/localStorage. The access-token-in-memory + silent-refresh intent is preserved.

---

## 3. Architecture

New top-level **`frontend/`** package (sibling of `backend/`). The backend is untouched.

```
frontend/
  index.html
  vite.config.ts            # React plugin + dev proxy /api -> :8000 + Vitest config
  tsconfig.json
  tailwind.config.ts, postcss.config.js
  package.json
  .env / .env.example       # VITE_API_BASE (default "/api")
  src/
    main.tsx                # React root; mounts <App/> with providers
    App.tsx                 # Router + provider tree (QueryClient, AuthProvider)
    routes/
      LoginPage.tsx, RegisterPage.tsx
      DocumentsPage.tsx
      ChatPage.tsx          # /chat and /chat/:conversationId
      ProtectedRoute.tsx    # redirects to /login when unauthenticated
    components/
      layout/ (AppShell, Sidebar, TopBar)
      chat/ (MessageList, MessageBubble, Citations, ChatInput, StreamingMessage)
      documents/ (UploadDropzone, DocumentList, DocumentRow, MetadataFields)
      ui/ (shadcn/ui primitives: button, input, dialog, scroll-area, toast, ...)
    api/
      schema.ts             # GENERATED by openapi-typescript (do not hand-edit)
      client.ts             # openapi-fetch client + auth middleware (attach access token, 401->refresh)
      chatStream.ts         # POST SSE reader (fetch + ReadableStream -> async iterator of frames)
      hooks/ (useDocuments, useUploadDocument, useDeleteDocument,
              useConversations, useConversation, useChat, useAuth)
    auth/
      AuthContext.tsx       # in-memory access token + user; login/register/logout/refresh
    lib/ (cn(), formatting, query client setup)
    types/ (hand types for things outside the API, e.g. SSE frame shapes)
  tests/ (Vitest + RTL + MSW handlers)
```

### Routing & guarding
- Public: `/login`, `/register`. Protected (wrapped in `<ProtectedRoute>`): `/` → redirect to `/chat`,
  `/chat`, `/chat/:conversationId`, `/documents`.
- `ProtectedRoute` checks `AuthContext`: if no access token, attempt a silent refresh once; if that
  fails, redirect to `/login` (preserving intended destination).

### Layout (AppShell)
- **Sidebar:** "New chat" button, conversation list (newest first, active highlighted), nav links
  (Chat / Documents), user menu (email + logout).
- **Main pane:** the active route (chat thread or documents view).
- Responsive: sidebar collapses on narrow screens.

### Auth flow (access-in-memory + httpOnly refresh cookie)
1. **Login/Register** → `POST /auth/login` returns `{ access_token }` and sets the httpOnly refresh
   cookie. Store the access token in `AuthContext` (memory). Fetch `GET /auth/me` for the user.
2. **Every API request** attaches `Authorization: Bearer <access token>` via an `openapi-fetch`
   middleware; all requests use `credentials: "include"` so the cookie rides along.
3. **On `401`** (expired access token): the client middleware calls `POST /auth/refresh` once
   (cookie → new access token), updates context, and retries the original request. If refresh `401`s,
   clear context → redirect to `/login`.
4. **On app load:** if there's no access token in memory (e.g. after a page refresh), attempt
   `POST /auth/refresh` to silently restore the session before rendering protected routes.
5. **Logout** → `POST /auth/logout` (clears cookie) + drop the in-memory token + clear React Query cache.

### Data flow (TanStack Query)
- Queries: `useDocuments` (`GET /documents`, optional `?course=`), `useConversations`
  (`GET /conversations`), `useConversation(id)` (`GET /conversations/{id}` → history).
- Mutations: `useUploadDocument` (`POST /documents`, multipart, invalidates documents),
  `useDeleteDocument` (`DELETE /documents/{id}`), `useDeleteConversation`.
- Chat is **not** a normal query (it streams) — see below; after a turn completes we invalidate
  `conversations` (so the sidebar reorders/updates) and, for a brand-new chat, navigate to
  `/chat/:conversationId` using the id from the `meta` frame.

### Chat streaming (`chatStream.ts`)
`POST /chat` with `{ question, conversation_id?, course?, tags?, top_k? }` and
`Accept: text/event-stream`, read `response.body` as a stream, decode and split on `\n\n`, parse
`event:`/`data:` lines into typed frames:
- `meta { conversation_id }` → set/confirm the active conversation id.
- `token { delta }` → append to the in-progress assistant message (React state drives the live bubble).
- `citations [ ... ]` → attach source list to the finished message.
- `done {}` → finalize; invalidate `conversations`.
- `error { detail }` → show an inline error in the message thread; stop the stream.
The reader is an async iterator so the component can `for await` frames and update state incrementally.
401 during a chat stream → run the refresh flow, then restart the stream once.

---

## 4. Screens / UX

**Auth (`/login`, `/register`)** — centered card: email + password, validation, submit with loading
state, error toast on `401`/`409`, link between login/register. On success → redirect to `/chat`.

**Documents (`/documents`)** — `UploadDropzone` (drag/drop or click; shows the picked filename) +
optional title/course/tags fields → upload button with progress/disabled state. Below: a list of the
user's documents (title/filename, course, chunk count, size, date) each with a delete action
(confirm dialog). Surface `400` (unsupported/empty), `413` (too large), `409` (duplicate → point at the
existing doc) as clear messages.

**Chat (`/chat`, `/chat/:conversationId`)** — sidebar conversation list + main thread:
- Message list: user bubbles + assistant bubbles; the streaming assistant message renders tokens as
  they arrive (with a subtle cursor/typing indicator).
- **Citations** under each assistant answer: a compact, expandable list (filename · section · score)
  matching the inline `[n]` markers; clicking a citation can scroll to / highlight it (link to the
  document where feasible).
- `ChatInput`: multiline textarea, Enter to send / Shift+Enter newline, disabled while streaming, with
  a stop affordance. Optional "advanced" controls (course/tags filter, top_k) behind a small toggle.
- "New chat" clears the thread and posts with no `conversation_id`; on the `meta` frame, navigate to
  the new `/chat/:id`. Opening a sidebar item loads its history via `useConversation`.

Empty states (no documents yet → prompt to upload; no conversations → prompt to ask something),
loading skeletons, and toasts for errors throughout.

---

## 5. API integration

- **Type generation:** `npm run gen:api` → `openapi-typescript http://localhost:8000/openapi.json -o
  src/api/schema.ts`. Committed; regenerated whenever the backend API changes. (A saved
  `openapi.json` snapshot can seed CI so generation doesn't require a live backend.)
- **Typed client:** `openapi-fetch` `createClient<paths>({ baseUrl: import.meta.env.VITE_API_BASE,
  credentials: "include" })` + a middleware that injects the bearer token and implements the
  401→refresh→retry logic. The chat SSE endpoint bypasses the typed client (uses `fetch` directly via
  `chatStream.ts`) but reuses the same base URL + auth header + refresh logic.
- **Endpoints consumed:** `POST /auth/register|login|refresh|logout`, `GET /auth/me`;
  `GET/POST /documents`, `DELETE /documents/{id}`; `POST /search` (optional, debug);
  `POST /chat` (SSE); `GET /conversations`, `GET /conversations/{id}`, `DELETE /conversations/{id}`.

---

## 6. Tooling / config

- **Vite** `server.proxy`: `"/api": { target: "http://localhost:8000", changeOrigin: true,
  rewrite: p => p.replace(/^\/api/, "") }` so the app calls `/api/...` same-origin in dev.
  `VITE_API_BASE` defaults to `/api`.
- **Tailwind** configured for `src/**`; shadcn/ui initialized (components land in `src/components/ui`).
- **Scripts:** `dev` (vite), `build` (tsc + vite build), `preview`, `lint` (eslint),
  `typecheck` (tsc --noEmit), `test` (vitest), `gen:api`.
- **Lint/format:** ESLint (typescript-eslint, react-hooks, jsx-a11y) + Prettier.
- A **root `Makefile` target** (e.g. `make frontend`) and a README section document running both
  servers together (`make db` + `make dev` + `cd frontend && npm run dev`).

---

## 7. Testing (Vitest + RTL + MSW)

- **Unit/hook:** the SSE frame parser (`chatStream.ts`) against canned byte chunks (partial frames,
  multiple frames per chunk, `error` frame); the auth middleware's 401→refresh→retry; query hooks with
  MSW-mocked endpoints.
- **Component:** Login/Register (validation, error states), UploadDropzone (file select, `413`/`409`
  surfaced), DocumentList (render + delete), ChatPage (send → streamed tokens render → citations show
  → follow-up), Sidebar (lists conversations, "new chat", active highlight). MSW provides mocked API
  incl. a streamed `/chat` response.
- **Routing/guard:** ProtectedRoute redirects unauthenticated → `/login`; silent-refresh-on-load path.
- No live backend or API key needed (MSW mocks everything). Optional Playwright e2e is **deferred**.

---

## 8. Out of scope (deferred)

Production Docker image + nginx + CI for the frontend (**Phase 5**); E2E (Playwright); SSR/Next.js (this
is a Vite SPA); i18n; theming beyond light/dark token setup; offline/PWA; real-time multi-device sync;
rich document preview/inline PDF viewer (citations link/scroll only); admin/multi-tenant UI; the
deferred backend retrieval/UX items from Phase 3 (rerank/HyDE, per-message citation history).

---

## 9. Risks / notes

- **Cross-origin cookies:** the refresh cookie is `path=/auth`, httpOnly, `secure`/`samesite` from
  settings. The Vite proxy keeps dev same-origin so the cookie flows without CORS/SameSite friction;
  for a deployed split-origin setup (Phase 5) the cookie attributes + CORS `allow_credentials` must be
  revisited (`secure=true`, appropriate `samesite`).
- **POST SSE parsing:** must handle frames split across chunk boundaries and multiple frames per chunk;
  the parser buffers until `\n\n`. Tested explicitly.
- **401 mid-stream:** a chat stream can start, then the access token expires on a later request; the
  refresh-and-retry must also cover the streaming path (restart the stream once).
- **Inline `[n]` vs deduped citations:** the backend now dedupes citations by document (Phase 3 A-I2),
  while the answer text uses per-chunk `[n]` markers — the UI maps `[n]` to source documents
  best-effort and shows the citation list as "sources", not a strict 1:1 index.
- **Type generation needs the schema:** `gen:api` hits the running backend (or a committed
  `openapi.json` snapshot). Document both paths so a fresh clone / CI can generate without a live server.
- **No backend changes expected.** If a genuine gap appears (e.g. CORS tweak, a missing field), it is
  surfaced for a separate small backend change — not silently bundled into the frontend.
