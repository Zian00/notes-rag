import { http, HttpResponse } from "msw"
import { server } from "./msw/server"
import { fetchClient, setAccessToken, setOnAuthFailure } from "@/api/client"

// client.ts resolves its relative "/api" base against window.location.origin —
// derive the test base the same way so the two can't drift (e.g. if jsdom's
// default test URL ever changes).
const API_BASE = `${window.location.origin}/api`

describe("api client auth middleware", () => {
  beforeEach(() => {
    // Reset the module-level in-memory token so tests don't leak state into each other.
    setAccessToken(null)
    setOnAuthFailure(() => {})
  })

  it("refreshes the access token and retries the original request on a 401", async () => {
    let documentsCallCount = 0
    let capturedRetryAuthHeader: string | null = null
    let refreshCallCount = 0

    server.use(
      http.get(`${API_BASE}/documents`, ({ request }) => {
        documentsCallCount += 1
        if (documentsCallCount === 1) {
          // First call: simulate an expired access token.
          return new HttpResponse(null, { status: 401 })
        }
        // Second call (the retry): capture the Authorization header the middleware attached.
        capturedRetryAuthHeader = request.headers.get("Authorization")
        return HttpResponse.json({ documents: [] })
      }),
      http.post(`${API_BASE}/auth/refresh`, () => {
        refreshCallCount += 1
        return HttpResponse.json({ access_token: "new-token", token_type: "bearer" })
      }),
    )

    setAccessToken("expired-token")

    const { data, error, response } = await fetchClient.GET("/documents")

    expect(refreshCallCount).toBe(1)
    expect(documentsCallCount).toBe(2)
    expect(capturedRetryAuthHeader).toBe("Bearer new-token")
    expect(response.status).toBe(200)
    expect(error).toBeUndefined()
    expect(data).toEqual({ documents: [] })
  })

  it("refreshes the access token and retries a request WITH A BODY on a 401 (body must survive the retry)", async () => {
    // Regression test: openapi-fetch already calls fetch(request) before onResponse
    // runs, which consumes/locks the request body. Constructing `new Request(request, ...)`
    // from that spent request throws for POST/PUT/PATCH bodies. This test proves the
    // retry preserves the original body for a typed POST endpoint (/search).
    let searchCallCount = 0
    let capturedRetryAuthHeader: string | null = null
    let capturedRetryBody: unknown = null
    let refreshCallCount = 0

    server.use(
      http.post(`${API_BASE}/search`, async ({ request }) => {
        searchCallCount += 1
        if (searchCallCount === 1) {
          // First call: simulate an expired access token.
          return new HttpResponse(null, { status: 401 })
        }
        // Second call (the retry): capture the Authorization header and the body
        // the middleware actually sent, to prove the original body wasn't lost.
        capturedRetryAuthHeader = request.headers.get("Authorization")
        capturedRetryBody = await request.json()
        return HttpResponse.json([])
      }),
      http.post(`${API_BASE}/auth/refresh`, () => {
        refreshCallCount += 1
        return HttpResponse.json({ access_token: "new-token", token_type: "bearer" })
      }),
    )

    setAccessToken("expired-token")

    const { data, error, response } = await fetchClient.POST("/search", {
      body: { query: "test query", top_k: 3 },
    })

    expect(refreshCallCount).toBe(1)
    expect(searchCallCount).toBe(2)
    expect(capturedRetryAuthHeader).toBe("Bearer new-token")
    expect(capturedRetryBody).toEqual({ query: "test query", top_k: 3 })
    expect(response.status).toBe(200)
    expect(error).toBeUndefined()
    expect(data).toEqual([])
  })

  it("calls onAuthFailure and surfaces the 401 when refresh itself fails", async () => {
    let refreshCallCount = 0

    server.use(
      http.get(`${API_BASE}/documents`, () => new HttpResponse(null, { status: 401 })),
      http.post(`${API_BASE}/auth/refresh`, () => {
        refreshCallCount += 1
        return new HttpResponse(null, { status: 401 })
      }),
    )

    const onAuthFailure = vi.fn()
    setOnAuthFailure(onAuthFailure)
    setAccessToken("expired-token")

    const { response } = await fetchClient.GET("/documents")

    expect(refreshCallCount).toBe(1)
    expect(onAuthFailure).toHaveBeenCalledTimes(1)
    expect(response.status).toBe(401)
  })

  it("does not attempt a refresh on a 401 from an /auth/* endpoint (anti-loop guard)", async () => {
    let refreshCallCount = 0

    server.use(
      http.post(`${API_BASE}/auth/login`, () => new HttpResponse(null, { status: 401 })),
      http.post(`${API_BASE}/auth/refresh`, () => {
        refreshCallCount += 1
        return HttpResponse.json({ access_token: "new-token", token_type: "bearer" })
      }),
    )

    const { response } = await fetchClient.POST("/auth/login", {
      body: { email: "user@example.com", password: "password123" },
    })

    expect(refreshCallCount).toBe(0)
    expect(response.status).toBe(401)
  })
})
