# Frontend

React single-page application for Notes RAG. Provides document management, group organization, and a streaming chat interface backed by the agentic RAG API.

## Stack

- **React 19** + **TypeScript** — UI framework
- **Vite** — dev server and bundler
- **Tailwind CSS v4** + **shadcn/ui** — styling and component primitives
- **TanStack Query** — server state caching and synchronization
- **openapi-fetch** + **openapi-react-query** — typed API client generated from the backend's OpenAPI schema
- **React Router** — client-side routing
- **Vitest** + **React Testing Library** + **MSW** — testing with mocked API responses

## Local Setup

The frontend needs the backend running to proxy API requests.

```bash
# 1. Start the backend (see backend/README.md or repo root README)
make db && make dev   # from repo root

# 2. Install dependencies
npm install

# 3. Start the dev server
npm run dev           # http://localhost:5173
```

The Vite dev server proxies `/api/*` to `http://localhost:8000`, stripping the `/api` prefix. No `.env` file or CORS config is needed locally.

## Commands

| Command | Description |
|---------|-------------|
| `npm run dev` | Start Vite dev server with hot reload |
| `npm run build` | Type-check and build for production (`dist/`) |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run test` | Run Vitest suite (123 tests) |
| `npm run gen:api` | Regenerate `src/api/schema.ts` from `openapi.json` |

## API Client

The API layer is fully typed end-to-end:

1. The backend exposes an OpenAPI schema at `/openapi.json`
2. `openapi-typescript` generates TypeScript types (`src/api/schema.ts`)
3. `openapi-fetch` creates a typed fetch client
4. `openapi-react-query` wraps it into TanStack Query hooks (`$api`)

After backend API changes, regenerate the types:

```bash
# Fetch fresh schema from running backend, then generate
curl http://localhost:8000/openapi.json -o openapi.json
npm run gen:api
```

Or use the committed `openapi.json` snapshot directly with `npm run gen:api`.

## Key Architecture

```
src/
  api/
    client.ts       Fetch client, auth middleware, token refresh
    hooks/          TanStack Query hooks (useDocuments, useChat, useGroups, ...)
    chatStream.ts   SSE streaming parser for chat responses
    schema.ts       Generated OpenAPI types
  auth/             AuthContext, login/register flows
  components/
    chat/           ChatInput, MessageBubble, MessageList, AttachmentCard, AttachmentChip
    documents/      DocumentList, UploadDropzone, GroupSelect
    layout/         AppShell, Sidebar, ThemeToggle
    ui/             shadcn/ui primitives
  routes/           Page components (ChatPage, DocumentsPage, LoginPage, ...)
  lib/              Utilities (validation, formatting)
```

**Auth** — JWT access tokens are held in memory (never localStorage) to limit XSS exposure. Refresh tokens are httpOnly cookies. The `authMiddleware` in `client.ts` handles transparent token refresh on 401 responses.

**Chat streaming** — The chat endpoint returns server-sent events. `chatStream.ts` parses the SSE stream and yields tokens, citations, and the thinking indicator to the UI in real time.

**Attachments** — Users can attach up to 5 files from the chat composer. Files upload via the documents API, and their IDs are sent with the chat message. Attachment cards in message history use authenticated `fetch` + blob URLs for downloads.

## Production Build

In production, the frontend is built inside a multi-stage Docker image (`Dockerfile`). The first stage runs `npm ci` and `npm run build`; the second stage copies the built `dist/` into a Caddy image that serves the SPA and reverse-proxies `/api` to the backend.
