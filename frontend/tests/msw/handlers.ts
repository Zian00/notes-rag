import { http, HttpResponse, type HttpHandler } from "msw"

// Derived the same way client.ts resolves its relative "/api" base — see
// tests/client.test.ts for why (keeps the mock URL from drifting from the app's).
const API_BASE = `${window.location.origin}/api`

// Default handlers applied to every test unless overridden with server.use(...).
// <App/> now fires an on-mount /auth/refresh (AuthContext's silent refresh) —
// without a default handler here, any test rendering <App/> (e.g. smoke.test.tsx)
// would hit the real network. Defaulting to 401 makes "anon" the safe default state.
const handlers: HttpHandler[] = [
  http.post(`${API_BASE}/auth/refresh`, () => new HttpResponse(null, { status: 401 })),
]

export { handlers }
