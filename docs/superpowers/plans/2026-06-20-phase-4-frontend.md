# Phase 4 — Frontend (React) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. For the UI-building milestones (C–E), also load **artifact-design** / **frontend-design** for visual quality.

**Goal:** A React + Vite + TypeScript SPA in a new top-level `frontend/` that consumes the existing backend: register/login (access token in memory + httpOnly refresh cookie + silent refresh), upload & manage documents, and chat with **streaming, cited** answers inside a conversation sidebar. Typed end-to-end from the backend's OpenAPI. **No backend changes.**

**Architecture:** SPA per the spec. Tailwind v4 + shadcn/ui for UI; TanStack Query (via `openapi-react-query`) for server state; a light `AuthContext` for the in-memory access token; `openapi-fetch` typed client with an auth middleware (bearer + 401→refresh→retry); a `fetch`+`ReadableStream` SSE reader for the POST `/chat` stream; React Router for routing/guarding. Vite dev proxy `/api → :8000` keeps dev same-origin (cookies + CORS just work).

**Tech Stack:** React 18, Vite, TypeScript, React Router, Tailwind CSS **v4** (`@tailwindcss/vite`), shadcn/ui, `@tanstack/react-query` + `openapi-react-query`, `openapi-fetch`, `openapi-typescript` (dev), Vitest + React Testing Library + MSW, ESLint + Prettier. Package manager: **npm**.

**Spec:** `docs/superpowers/specs/2026-06-20-frontend-design.md`

> **Commit policy (user standing rule):** NEVER auto-commit. STOP at each **milestone boundary** for the user to review + commit.
> **Standing constraints:** No backend changes (surface any genuine gap for a separate decision). Keep dependencies to those in the spec. Comment non-obvious code (learning project).

> **Backend must be running** for `gen:api` (type generation) and for any manual check. Use `make db` + `make dev` (backend at :8000). The frontend never needs a Google API key (MSW mocks the API in tests).

---

## File Structure (target)

```
frontend/
  package.json  vite.config.ts  tsconfig*.json  index.html
  components.json  .env.example  .eslintrc / eslint.config.js  .prettierrc
  openapi.json                      # committed snapshot for offline gen:api / CI
  src/
    main.tsx  App.tsx  index.css
    api/ schema.ts(generated)  client.ts  reactQuery.ts  chatStream.ts
        hooks/ useDocuments.ts useConversations.ts useChat.ts
    auth/ AuthContext.tsx  useAuth.ts
    routes/ ProtectedRoute.tsx LoginPage.tsx RegisterPage.tsx DocumentsPage.tsx ChatPage.tsx
    components/ layout/(AppShell,Sidebar,TopBar)  chat/(MessageList,MessageBubble,StreamingMessage,Citations,ChatInput)
                documents/(UploadDropzone,DocumentList,DocumentRow,MetadataFields)  ui/(shadcn)
    lib/ cn.ts  queryClient.ts
  tests/ setup.ts  msw/(handlers.ts,server.ts)  *.test.tsx
```

---

## Milestone A — Scaffold, tooling, type generation

### Task 1: Scaffold the Vite app + Tailwind v4 + path aliases
**Files:** new `frontend/` (run from repo root)
- [x] Scaffold: `npm create vite@latest frontend -- --template react-ts` then `cd frontend && npm install`.
- [x] Tailwind v4: `npm install tailwindcss @tailwindcss/vite`. Replace `src/index.css` contents with `@import "tailwindcss";` (plus a `@theme`/`:root` block for tokens later).
- [x] Path aliases: in `tsconfig.json` + `tsconfig.app.json` add `"baseUrl": "."` and `"paths": { "@/*": ["./src/*"] }`. `npm i -D @types/node`.
- [x] `vite.config.ts`: add the `@tailwindcss/vite` plugin, `resolve.alias` `{ "@": path.resolve(__dirname, "./src") }`, and the **dev proxy**:
```ts
server: { proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true, rewrite: p => p.replace(/^\/api/, "") } } }
```
- [x] Acceptance: `npm run dev` serves the default page; `npm run build` succeeds.

### Task 2: shadcn/ui init + base components
- [x] `npx shadcn@latest init` (Vite + TS; CSS variables yes; base color slate). Confirm `components.json` aliases (`@/components`, `@/lib/utils`) and that it wrote the CSS variables into `index.css`.
- [x] Add the primitives we'll use: `npx shadcn@latest add button input textarea label card dialog scroll-area sonner skeleton dropdown-menu badge`.
- [x] Acceptance: import `Button` in `App.tsx`, render it, `npm run dev` shows it styled.

### Task 3: Lint, format, test harness, scripts
- [x] ESLint (typescript-eslint, react-hooks, jsx-a11y) + Prettier configs. Vitest + RTL + jsdom + MSW: `npm i -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom msw`. Add `tests/setup.ts` (jest-dom + MSW server lifecycle) and Vitest config block in `vite.config.ts` (`test: { environment: "jsdom", setupFiles: "./tests/setup.ts", globals: true }`).
- [x] `package.json` scripts: `dev`, `build` (`tsc -b && vite build`), `preview`, `lint`, `typecheck` (`tsc --noEmit`), `test` (`vitest run`), `test:watch`, `gen:api`.
- [x] One trivial passing test (`tests/smoke.test.tsx` renders `<App/>`) to prove the harness.
- [x] Acceptance: `npm run lint`, `npm run typecheck`, `npm run test` all green.

### Task 4: Generate API types
- [x] `npm i -D openapi-typescript` and `npm i openapi-fetch openapi-react-query @tanstack/react-query`.
- [x] With the backend running, save a snapshot + generate: `gen:api` = `openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.ts` (also `curl .../openapi.json -o openapi.json` committed for offline/CI). Run it.
- [x] Acceptance: `src/api/schema.ts` exists with `paths`/`components`; `npm run typecheck` clean.
- [x] **MILESTONE A — STOP.** Suggested commit: `feat(phase4): scaffold Vite+TS+Tailwind v4+shadcn, tooling, generated API types`.

---

## Milestone B — Typed client, auth, routing shell

### Task 5: Typed client + auth middleware
**Files:** `src/api/client.ts`, `src/api/reactQuery.ts`, `src/lib/queryClient.ts`
- [ ] `client.ts`: an in-memory access-token holder + the fetch client + a middleware that adds the bearer header and does 401→refresh→retry. Key code:
```ts
import createFetchClient, { type Middleware } from "openapi-fetch";
import createReactQueryClient from "openapi-react-query";
import type { paths } from "./schema";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

let accessToken: string | null = null;
export const setAccessToken = (t: string | null) => { accessToken = t; };
export const getAccessToken = () => accessToken;

// Called on 401; hits /auth/refresh (httpOnly cookie sent via credentials:"include").
let onAuthFailure: () => void = () => {};
export const setOnAuthFailure = (fn: () => void) => { onAuthFailure = fn; };

async function refreshAccessToken(): Promise<boolean> {
  const res = await fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "include" });
  if (!res.ok) return false;
  const data = await res.json();
  setAccessToken(data.access_token);
  return true;
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    if (accessToken) request.headers.set("Authorization", `Bearer ${accessToken}`);
    return request;
  },
  async onResponse({ request, response }) {
    if (response.status !== 401 || request.url.includes("/auth/")) return response;
    // try one silent refresh + retry
    if (await refreshAccessToken()) {
      const retry = new Request(request, { headers: request.headers });
      retry.headers.set("Authorization", `Bearer ${accessToken}`);
      return fetch(retry);
    }
    onAuthFailure();
    return response;
  },
};

export const fetchClient = createFetchClient<paths>({ baseUrl: BASE, credentials: "include" });
fetchClient.use(authMiddleware);
export const $api = createReactQueryClient(fetchClient);
export { refreshAccessToken };
```
- [ ] `lib/queryClient.ts`: a `QueryClient` with sane defaults (retry: false for 4xx, staleTime). `reactQuery.ts` re-exports `$api`.
- [ ] Tests (`tests/client.test.ts`, MSW): a 401 on `/documents` triggers `/auth/refresh` then a retry with the new token; a failing refresh calls `onAuthFailure`. `/auth/*` 401s do NOT loop.

### Task 6: AuthContext + silent refresh + providers
**Files:** `src/auth/AuthContext.tsx`, `src/auth/useAuth.ts`, `src/App.tsx`, `src/main.tsx`
- [ ] `AuthContext`: state `{ user, status: "loading"|"authed"|"anon" }`; methods `login(email,pw)`, `register(...)`, `logout()`. `login` → `POST /auth/login` (typed) → `setAccessToken` + `GET /auth/me` → user. On mount, run `refreshAccessToken()` once (silent restore); set status accordingly. Wire `setOnAuthFailure(() => { setAccessToken(null); setStatus("anon") })`.
- [ ] `App.tsx`: provider tree `QueryClientProvider > AuthProvider > BrowserRouter > Routes`; `<Toaster/>` (sonner) mounted.
- [ ] Tests: AuthContext login sets user; logout clears; on-mount refresh success → authed, failure → anon (MSW).

### Task 7: Routing + ProtectedRoute + Login/Register + AppShell skeleton
**Files:** `routes/*`, `components/layout/*`
- [ ] Routes: `/login`, `/register` (public); `/` → redirect `/chat`; `/chat`, `/chat/:conversationId`, `/documents` wrapped in `<ProtectedRoute>`.
- [ ] `ProtectedRoute`: while `status==="loading"` show a spinner; `anon` → `<Navigate to="/login" state={{from}}/>`; `authed` → render `<AppShell><Outlet/></AppShell>`.
- [ ] `LoginPage`/`RegisterPage`: shadcn `Card` + `Input` form, validation, submit-loading, error toast on 401/409, link between them; on success → navigate to intended route or `/chat`.
- [ ] `AppShell`: `Sidebar` (placeholder nav: Chat / Documents, user email + logout) + main `<Outlet/>`. Responsive (sidebar collapsible).
- [ ] Tests: ProtectedRoute redirects anon → /login; login form happy path navigates; logout returns to /login.
- [ ] **MILESTONE B — STOP.** Suggested commit: `feat(phase4): typed client + auth middleware, AuthContext silent refresh, routing shell`.

---

## Milestone C — Documents

### Task 8: Document hooks
**Files:** `src/api/hooks/useDocuments.ts`
- [ ] `useDocuments(course?)` → `$api.useQuery("get", "/documents", { params: { query: { course } } })`. `useUploadDocument()` → mutation posting multipart to `/documents` (use `fetchClient.POST` with a `FormData` body; openapi-fetch supports `body` as FormData with `bodySerializer`), invalidates `["get","/documents"]`. `useDeleteDocument()` → `DELETE /documents/{id}`, invalidates.
- [ ] Tests (MSW): list renders; upload invalidates + refetches; delete removes.

### Task 9: Documents UI
**Files:** `routes/DocumentsPage.tsx`, `components/documents/*`
- [ ] `UploadDropzone` (drag/drop + click; shows picked filename) + `MetadataFields` (title/course/tags) + upload button (progress/disabled). Surface `400` (unsupported/empty), `413` (too large), `409` (duplicate → message referencing existing doc) as inline errors/toasts.
- [ ] `DocumentList`/`DocumentRow`: title/filename, course, chunk_count, size, date; delete with confirm `Dialog`. Empty state ("upload your first note").
- [ ] Loading skeletons; errors via toast. Use artifact-design/frontend-design for layout quality.
- [ ] Tests: upload flow (success + 413/409 surfaced), delete confirm + removal, empty state.
- [ ] **MILESTONE C — STOP.** Suggested commit: `feat(phase4): documents upload/list/delete with typed hooks`.

---

## Milestone D — Chat + streaming (the core)

### Task 10: SSE stream reader
**Files:** `src/api/chatStream.ts`; Test `tests/chatStream.test.ts`
- [ ] An async generator that POSTs to `/chat` and yields typed frames. Key code:
```ts
import { getAccessToken, refreshAccessToken } from "./client";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
export type ChatFrame =
  | { event: "meta"; data: { conversation_id: string } }
  | { event: "token"; data: { delta: string } }
  | { event: "citations"; data: Citation[] }
  | { event: "done"; data: Record<string, never> }
  | { event: "error"; data: { detail: string } };

export async function* streamChat(body: ChatRequestBody, signal?: AbortSignal): AsyncGenerator<ChatFrame> {
  const doFetch = () => fetch(`${BASE}/chat`, {
    method: "POST", credentials: "include", signal,
    headers: { "Content-Type": "application/json", Accept: "text/event-stream",
               ...(getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {}) },
    body: JSON.stringify(body),
  });
  let res = await doFetch();
  if (res.status === 401 && (await refreshAccessToken())) res = await doFetch(); // retry once
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {       // frames are separated by a blank line
      const raw = buf.slice(0, i); buf = buf.slice(i + 2);
      const evLine = raw.split("\n").find(l => l.startsWith("event:"));
      const dataLine = raw.split("\n").find(l => l.startsWith("data:"));
      if (!evLine || !dataLine) continue;
      yield { event: evLine.slice(6).trim(), data: JSON.parse(dataLine.slice(5).trim()) } as ChatFrame;
    }
  }
}
```
- [ ] Tests: feed canned `ReadableStream` chunks — a full happy sequence; a frame split across two chunks; multiple frames in one chunk; an `error` frame. Assert the yielded frames.

### Task 11: useChat hook + conversation hooks
**Files:** `src/api/hooks/useChat.ts`, `src/api/hooks/useConversations.ts`
- [ ] `useConversations()` → `GET /conversations`; `useConversation(id)` → `GET /conversations/{id}` (history); `useDeleteConversation()`.
- [ ] `useChat()`: local state for the message list + a `send(question)` that appends the user message, opens `streamChat`, appends an assistant message and grows its `content` on each `token`, attaches `citations`, captures `conversation_id` from `meta` (and navigates to `/chat/:id` for a new chat), and on `done` invalidates `["get","/conversations"]`. Tracks `isStreaming`; supports abort.
- [ ] Tests (MSW streamed response): `send` produces a growing assistant message then citations; new-chat navigation on `meta`; conversation list invalidated on `done`.

### Task 12: Chat UI
**Files:** `routes/ChatPage.tsx`, `components/chat/*`, `components/layout/Sidebar.tsx`
- [ ] `Sidebar`: "New chat" button, conversation list (newest first, active highlighted, delete on hover), opens history. `ChatPage`: `MessageList` (user/assistant `MessageBubble`s), `StreamingMessage` (renders tokens live + typing cursor), `Citations` (expandable list: filename · section · score, mapped from `[n]`), `ChatInput` (textarea, Enter=send/Shift+Enter=newline, disabled while streaming + stop button), optional advanced filters (course/tags/top_k) behind a toggle.
- [ ] Loading history skeleton; empty state ("ask something about your notes"). Apply artifact-design/frontend-design.
- [ ] Tests: send → streamed tokens render → citations show; open a sidebar conversation loads history; new chat resets + navigates.
- [ ] **MILESTONE D — STOP.** Suggested commit: `feat(phase4): chat streaming (SSE) + conversations sidebar/history`.

---

## Milestone E — Polish, docs, verification

### Task 13: Polish
- [ ] Light/dark token pass; responsive sidebar (drawer on mobile); consistent toasts for all mutation errors; focus/keyboard a11y on dialogs + chat input; favicon/title.

### Task 14: Docs + dev ergonomics
- [ ] `docs/learning/04-frontend.md` (match the `0x-*.md` tone): Vite proxy + same-origin cookies, the access-in-memory + httpOnly-refresh + silent-refresh flow, generated OpenAPI types + typed client + react-query, the POST-SSE reader, and the component layout. Honest "deferred" note (Playwright e2e, prod Docker → Phase 5).
- [ ] README: add a "Frontend (local dev)" section (`cd frontend && npm install && npm run dev`; needs backend at :8000; `npm run gen:api` after backend API changes). Optional root `Makefile` target `frontend`.

### Task 15: Final verification
- [ ] `cd frontend && npm run lint && npm run typecheck && npm run test && npm run build` all green.
- [ ] Manual smoke (backend up): register → upload a note → chat (watch tokens stream + citations) → follow-up in same conversation → reload (silent refresh keeps session) → logout.
- [ ] **MILESTONE E — STOP.** Suggested commit: `feat(phase4): polish, learning doc, README, final verification`.

---

## Risks / execution notes
- **Tailwind v4 is CSS-first** (no `tailwind.config.js` content array needed; `@tailwindcss/vite` plugin + `@import "tailwindcss"`). Don't reintroduce v3-style config. Confirm shadcn init targets v4.
- **Multipart upload via openapi-fetch:** pass a `FormData` body and let the browser set the boundary (don't hand-set `Content-Type`); openapi-fetch may need `bodySerializer: (b) => b` for FormData. Verify during Task 8.
- **POST-SSE parsing:** buffer until `\n\n`; handle split/many-per-chunk frames (tested). `EventSource` is NOT usable (GET-only).
- **401 during streaming** must refresh+retry once (handled in `streamChat`).
- **gen:api needs the schema:** backend running OR the committed `openapi.json` snapshot. Document both.
- **Citations vs inline [n]:** backend dedupes citations by document (Phase 3 A-I2); show the list as "sources", map `[n]` best-effort.
- **No backend changes.** If something's missing (CORS, a field), surface it as a separate decision — don't patch silently.
- **MSW for the streamed endpoint:** the mock must return a `ReadableStream`/`Response` with `text/event-stream` so chat tests exercise the real parser.
