import createFetchClient, { type Middleware } from "openapi-fetch"
import createReactQueryClient from "openapi-react-query"
import type { paths, components } from "./schema"

// No .env file exists yet, so VITE_API_BASE is undefined at runtime and the
// dev-server proxy path ("/api" -> backend, see vite.config.ts) is used instead.
const RAW_BASE = import.meta.env.VITE_API_BASE ?? "/api"

// openapi-fetch builds requests with the platform's Request/URL constructors,
// which (unlike a browser resolving a relative <a href>) require an absolute
// URL — a bare "/api" throws "Invalid URL" under Node's fetch (used by Vitest).
// Resolving against window.location.origin keeps the relative "/api" config
// (needed for the dev-server proxy) working in both the browser and tests.
const BASE = new URL(RAW_BASE, window.location.origin).toString().replace(/\/$/, "")
// Exported so other modules that need to issue a raw `fetch` (e.g. the SSE
// chat stream reader, which can't use openapi-fetch) resolve the same
// absolute base instead of re-deriving it and risking drift.
export const API_BASE = BASE

// Access token lives in memory only (never localStorage/sessionStorage) so it
// can't be read by an XSS payload; the refresh token is an httpOnly cookie
// that JS never touches at all.
let accessToken: string | null = null
export const setAccessToken = (t: string | null) => {
  accessToken = t
}
export const getAccessToken = () => accessToken

// Called when a silent refresh fails; AuthContext (later task) wires this to
// clear the session and redirect to /login.
let onAuthFailure: () => void = () => {}
export const setOnAuthFailure = (fn: () => void) => {
  onAuthFailure = fn
}

// Hits /auth/refresh (httpOnly cookie sent via credentials:"include"); stores the new access token.
async function refreshAccessToken(): Promise<boolean> {
  const res = await fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "include" })
  if (!res.ok) return false
  const data = (await res.json()) as components["schemas"]["TokenResponse"]
  setAccessToken(data.access_token)
  return true
}

// By the time onResponse runs, openapi-fetch has already called fetch(request),
// which consumes the request's body stream. `new Request(request, ...)` on that
// spent request throws for any POST/PUT/PATCH with a body. So we buffer the body
// in onRequest (before it's ever read) and stash it here, keyed by the middleware's
// per-request id, for onResponse to use when rebuilding the retry request. Entries
// are deleted as soon as they're consumed (or when unused) to avoid leaking memory.
const pendingRequestBodies = new Map<string, ArrayBuffer>()

const authMiddleware: Middleware = {
  async onRequest({ request, id }) {
    if (accessToken) request.headers.set("Authorization", `Bearer ${accessToken}`)
    if (request.body) {
      // .clone() here reads a copy of the stream; the original `request` returned
      // below still has its body intact for openapi-fetch's own fetch(request) call.
      pendingRequestBodies.set(id, await request.clone().arrayBuffer())
    }
    return request
  },
  async onResponse({ request, response, id }) {
    // Only intercept 401s. Never attempt a refresh for a 401 coming from /auth/*
    // itself (e.g. a bad login, or the refresh call failing) — otherwise a failed
    // refresh would trigger another refresh attempt and loop forever.
    if (response.status !== 401 || request.url.includes("/auth/")) {
      pendingRequestBodies.delete(id)
      return response
    }

    const bufferedBody = pendingRequestBodies.get(id)
    pendingRequestBodies.delete(id)

    if (await refreshAccessToken()) {
      // Build a fresh Request from the original url/method/headers (the original
      // `request` object is already spent and can't be reused) plus the buffered
      // body, then retry once with the refreshed bearer token.
      const retryHeaders = new Headers(request.headers)
      retryHeaders.set("Authorization", `Bearer ${accessToken}`)
      const retry = new Request(request.url, {
        method: request.method,
        headers: retryHeaders,
        credentials: request.credentials,
        body: bufferedBody,
      })
      return fetch(retry)
    }

    // Refresh failed (refresh cookie missing/expired) — session is unrecoverable.
    onAuthFailure()
    return response
  },
}

export const fetchClient = createFetchClient<paths>({ baseUrl: BASE, credentials: "include" })
fetchClient.use(authMiddleware)
export const $api = createReactQueryClient(fetchClient)
export { refreshAccessToken }
