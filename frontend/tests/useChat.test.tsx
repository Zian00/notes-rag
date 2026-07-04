import { renderHook, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useChat } from "@/api/hooks/useChat"
import { getConversationsListKey } from "@/api/hooks/useConversations"
import type { ChatFrame, ChatRequestBody, Citation } from "@/api/chatStream"

// Isolates the hook's state machine from the SSE parser (already covered by
// tests/chatStream.test.ts) by mocking streamChat to yield a scripted frame
// sequence directly.
vi.mock("@/api/chatStream", () => ({
  streamChat: vi.fn(),
}))

import { streamChat } from "@/api/chatStream"

const mockStreamChat = vi.mocked(streamChat)

// Builds an async generator from a fixed list of frames, mimicking streamChat's
// return shape without touching the real fetch/SSE machinery. `signal` is
// accepted (unused by default) so call sites match streamChat's real signature.
function scriptedStream(frames: ChatFrame[]) {
  return async function* (): AsyncGenerator<ChatFrame> {
    for (const frame of frames) {
      yield frame
    }
  }
}

// A stream that never resolves on its own — used for stop()/abort tests where
// the test drives cancellation rather than letting the generator finish. Rejects
// once the signal is aborted, mirroring a real aborted fetch.
function hangingStream() {
  return async function* (_body: ChatRequestBody, signal?: AbortSignal): AsyncGenerator<ChatFrame> {
    void _body
    yield { event: "meta", data: { conversation_id: "convo-hang" } }
    await new Promise<void>((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))
    })
  }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return { Wrapper, queryClient }
}

afterEach(() => {
  mockStreamChat.mockReset()
})

describe("useChat", () => {
  it("grows the assistant message with streamed tokens and attaches citations", async () => {
    const citation: Citation = { chunk_id: "c1", filename: "notes.pdf" }
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-1" } },
        { event: "token", data: { delta: "Hel" } },
        { event: "token", data: { delta: "lo" } },
        { event: "citations", data: [citation] },
        { event: "done", data: {} },
      ]),
    )

    const { Wrapper } = createWrapper()
    const { result } = renderHook(() => useChat(), { wrapper: Wrapper })

    await act(async () => {
      await result.current.send("hi")
    })

    await waitFor(() => expect(result.current.isStreaming).toBe(false))

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[0]).toMatchObject({ role: "user", content: "hi" })
    expect(result.current.messages[1]).toMatchObject({
      role: "assistant",
      content: "Hello",
      citations: [citation],
    })
  })

  it("calls onConversationCreated once for a new chat and reuses the id on follow-up", async () => {
    mockStreamChat.mockImplementationOnce(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "hi there" } },
        { event: "done", data: {} },
      ]),
    )
    mockStreamChat.mockImplementationOnce(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "again" } },
        { event: "done", data: {} },
      ]),
    )

    const onConversationCreated = vi.fn()
    const { Wrapper } = createWrapper()
    const { result } = renderHook(() => useChat({ onConversationCreated }), { wrapper: Wrapper })

    await act(async () => {
      await result.current.send("first question")
    })
    await waitFor(() => expect(result.current.isStreaming).toBe(false))

    expect(onConversationCreated).toHaveBeenCalledTimes(1)
    expect(onConversationCreated).toHaveBeenCalledWith("convo-new")

    await act(async () => {
      await result.current.send("second question")
    })
    await waitFor(() => expect(result.current.isStreaming).toBe(false))

    // Still only called once — a follow-up in the same session isn't a "new" chat.
    expect(onConversationCreated).toHaveBeenCalledTimes(1)

    const secondCallBody = mockStreamChat.mock.calls[1][0]
    expect(secondCallBody.conversation_id).toBe("convo-new")
  })

  it("invalidates the conversations list query after done", async () => {
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-1" } },
        { event: "token", data: { delta: "ok" } },
        { event: "done", data: {} },
      ]),
    )

    const { Wrapper, queryClient } = createWrapper()
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(() => useChat(), { wrapper: Wrapper })

    await act(async () => {
      await result.current.send("hi")
    })

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: getConversationsListKey() }),
    )
  })

  it("marks the assistant message errored on an error frame without throwing", async () => {
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-1" } },
        { event: "token", data: { delta: "partial" } },
        { event: "error", data: { detail: "boom" } },
      ]),
    )

    const { Wrapper } = createWrapper()
    const { result } = renderHook(() => useChat(), { wrapper: Wrapper })

    await act(async () => {
      await result.current.send("hi")
    })

    await waitFor(() => expect(result.current.isStreaming).toBe(false))

    expect(result.current.messages[1].error).toBe(true)
  })

  it("does NOT wipe messages when the conversationId prop merely catches up to the id this hook self-assigned (new-chat navigation)", async () => {
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "c1" } },
        { event: "token", data: { delta: "A" } },
        { event: "done", data: {} },
      ]),
    )

    const onConversationCreated = vi.fn()
    const { Wrapper } = createWrapper()
    const { result, rerender } = renderHook(
      ({ conversationId }: { conversationId?: string }) => useChat({ conversationId, onConversationCreated }),
      { wrapper: Wrapper, initialProps: { conversationId: undefined as string | undefined } },
    )

    await act(async () => {
      await result.current.send("hi")
    })
    await waitFor(() => expect(result.current.isStreaming).toBe(false))

    expect(onConversationCreated).toHaveBeenCalledWith("c1")
    expect(result.current.messages).toHaveLength(2)

    // Simulate Task 12's post-navigation prop change: the route now reflects the
    // conversation id this hook already holds. The just-streamed turn must survive.
    rerender({ conversationId: "c1" })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[0]).toMatchObject({ role: "user", content: "hi" })
    expect(result.current.messages[1]).toMatchObject({ role: "assistant", content: "A" })
  })

  it("DOES clear messages when the conversationId prop changes to a genuinely different conversation", async () => {
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "c1" } },
        { event: "token", data: { delta: "A" } },
        { event: "done", data: {} },
      ]),
    )

    const { Wrapper } = createWrapper()
    const { result, rerender } = renderHook(
      ({ conversationId }: { conversationId?: string }) => useChat({ conversationId }),
      { wrapper: Wrapper, initialProps: { conversationId: undefined as string | undefined } },
    )

    await act(async () => {
      await result.current.send("hi")
    })
    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.messages).toHaveLength(2)

    // Sidebar navigation to a different, unrelated conversation.
    rerender({ conversationId: "c2" })

    expect(result.current.messages).toHaveLength(0)
  })

  it("stop() aborts the in-flight stream without surfacing an error", async () => {
    mockStreamChat.mockImplementation(hangingStream())

    const { Wrapper } = createWrapper()
    const { result } = renderHook(() => useChat(), { wrapper: Wrapper })

    let sendPromise: Promise<void>
    act(() => {
      sendPromise = result.current.send("hi")
    })

    await waitFor(() => expect(result.current.isStreaming).toBe(true))

    act(() => {
      result.current.stop()
    })

    await act(async () => {
      await sendPromise!
    })

    expect(result.current.isStreaming).toBe(false)
    // Aborting is not an error — the placeholder assistant message must not be
    // marked errored just because the user (or unmount) cancelled the stream.
    expect(result.current.messages[1]?.error).not.toBe(true)
  })
})
