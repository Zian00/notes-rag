import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { http, HttpResponse } from "msw"
import type { ReactNode } from "react"
import { server } from "./msw/server"
import {
  useConversations,
  useConversation,
  useDeleteConversation,
} from "@/api/hooks/useConversations"
import type { components } from "@/api/schema"

// client.ts resolves its relative "/api" base against window.location.origin —
// derive the test base the same way so the two can't drift (see tests/client.test.ts).
const API_BASE = `${window.location.origin}/api`

type ConversationResponse = components["schemas"]["ConversationResponse"]
type ConversationDetail = components["schemas"]["ConversationDetail"]

const convo1: ConversationResponse = {
  id: "11111111-1111-1111-1111-111111111111",
  title: "First chat",
  group_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

const convo2: ConversationResponse = {
  id: "22222222-2222-2222-2222-222222222222",
  title: null,
  group_id: null,
  created_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
}

const convo1Detail: ConversationDetail = {
  ...convo1,
  messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "hello" },
  ],
}

// Fresh QueryClient per test so cache state can't leak between tests (retry
// disabled so failed mutations/queries reject/settle immediately in assertions).
function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return Wrapper
}

describe("useConversations", () => {
  it("resolves with the conversations list", async () => {
    server.use(http.get(`${API_BASE}/conversations`, () => HttpResponse.json([convo1, convo2])))

    const { result } = renderHook(() => useConversations(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.error).toBeNull()
    expect(result.current.data).toEqual([convo1, convo2])
  })
})

describe("useConversation", () => {
  it("fetches the conversation detail when an id is provided", async () => {
    server.use(
      http.get(`${API_BASE}/conversations/${convo1.id}`, () => HttpResponse.json(convo1Detail))
    )

    const { result } = renderHook(() => useConversation(convo1.id), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toEqual(convo1Detail)
  })

  it("does not fire a request when id is undefined", async () => {
    let requestCount = 0
    server.use(
      http.get(`${API_BASE}/conversations/:conversationId`, () => {
        requestCount += 1
        return HttpResponse.json(convo1Detail)
      })
    )

    const { result } = renderHook(() => useConversation(undefined), { wrapper: createWrapper() })

    // Give any accidental fetch a chance to fire before asserting it didn't.
    await new Promise((resolve) => setTimeout(resolve, 10))

    expect(result.current.fetchStatus).toBe("idle")
    expect(requestCount).toBe(0)
  })
})

describe("useDeleteConversation", () => {
  it("deletes a conversation and invalidates the conversations list", async () => {
    let listCallCount = 0

    server.use(
      http.get(`${API_BASE}/conversations`, () => {
        listCallCount += 1
        return HttpResponse.json(listCallCount === 1 ? [convo1] : [])
      }),
      http.delete(
        `${API_BASE}/conversations/${convo1.id}`,
        () => new HttpResponse(null, { status: 204 })
      )
    )

    // Both hooks share a single renderHook() root — mirrors the documents hooks'
    // tests, where cross-hook cache observation only reliably propagates within
    // a single React root under this test setup.
    const { result } = renderHook(
      () => ({ list: useConversations(), delete: useDeleteConversation() }),
      { wrapper: createWrapper() }
    )
    await waitFor(() => expect(result.current.list.isLoading).toBe(false))
    expect(result.current.list.data).toEqual([convo1])

    result.current.delete.mutate(convo1.id)

    await waitFor(() => expect(result.current.delete.isSuccess).toBe(true))
    await waitFor(() => expect(result.current.list.data).toEqual([]))
    expect(listCallCount).toBe(2)
  })

  it("throws an error with status 404 when the conversation is already gone", async () => {
    server.use(
      http.delete(`${API_BASE}/conversations/${convo1.id}`, () =>
        HttpResponse.json({ detail: "Conversation not found" }, { status: 404 })
      )
    )

    const { result } = renderHook(() => useDeleteConversation(), { wrapper: createWrapper() })

    result.current.mutate(convo1.id)

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as { status?: number })?.status).toBe(404)
  })
})
