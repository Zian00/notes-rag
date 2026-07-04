import { streamChat, type ChatFrame } from "@/api/chatStream"
import * as client from "@/api/client"

// Builds a ReadableStream<Uint8Array> from a list of string chunks, so tests
// can control exactly how SSE bytes are split across reads (mid-frame,
// multiple-frames-per-chunk, etc.) — something a single `new Response(body)`
// string can't express.
function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  let i = 0
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]))
        i += 1
      } else {
        controller.close()
      }
    },
  })
}

// Like streamFromChunks, but records whether the consumer's reader was
// cancelled, and deliberately never closes on its own — a real SSE
// connection stays open until the server ends it, so this simulates the
// consumer bailing out (unmount / navigation / stop) while the server is
// still sending. (If the stream self-closed after the last chunk, the
// underlying source would already be "done" by the time the test calls
// cancel(), and cancel() on an already-closed source is a legitimate no-op
// — that would make the test pass for the wrong reason.)
function streamFromChunksTrackingCancel(chunks: string[]): {
  stream: ReadableStream<Uint8Array>
  cancelled: () => boolean
} {
  const encoder = new TextEncoder()
  let i = 0
  let cancelled = false
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]))
        i += 1
      }
      // No `else { controller.close() }` — stream intentionally stays open.
    },
    cancel() {
      cancelled = true
    },
  })
  return { stream, cancelled: () => cancelled }
}

function sseResponse(chunks: string[], status = 200): Response {
  return new Response(streamFromChunks(chunks), {
    status,
    headers: { "Content-Type": "text/event-stream" },
  })
}

async function collect(gen: AsyncGenerator<ChatFrame>): Promise<ChatFrame[]> {
  const frames: ChatFrame[] = []
  for await (const frame of gen) frames.push(frame)
  return frames
}

describe("streamChat", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("yields the full meta -> token* -> citations -> done sequence", async () => {
    const body =
      'event: meta\ndata: {"conversation_id":"11111111-1111-1111-1111-111111111111"}\n\n' +
      'event: token\ndata: {"delta":"Hello "}\n\n' +
      'event: token\ndata: {"delta":"world"}\n\n' +
      'event: citations\ndata: [{"chunk_id":"c1","document_id":"d1","filename":"a.pdf","title":"A","page_number":1,"section":null,"score":0.9}]\n\n' +
      "event: done\ndata: {}\n\n"

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(sseResponse([body]))

    const frames = await collect(streamChat({ question: "hi" }))

    expect(frames).toEqual([
      { event: "meta", data: { conversation_id: "11111111-1111-1111-1111-111111111111" } },
      { event: "token", data: { delta: "Hello " } },
      { event: "token", data: { delta: "world" } },
      {
        event: "citations",
        data: [
          {
            chunk_id: "c1",
            document_id: "d1",
            filename: "a.pdf",
            title: "A",
            page_number: 1,
            section: null,
            score: 0.9,
          },
        ],
      },
      { event: "done", data: {} },
    ])
  })

  it("reassembles a frame whose data line is split across two chunks", async () => {
    // Split mid-way through the `data:` line's JSON payload.
    const chunk1 = 'event: token\ndata: {"del'
    const chunk2 = 'ta":"partial"}\n\n'

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(sseResponse([chunk1, chunk2]))

    const frames = await collect(streamChat({ question: "hi" }))

    expect(frames).toEqual([{ event: "token", data: { delta: "partial" } }])
  })

  it("yields multiple frames delivered in a single chunk", async () => {
    const combined =
      'event: token\ndata: {"delta":"a"}\n\n' + 'event: token\ndata: {"delta":"b"}\n\n'

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(sseResponse([combined]))

    const frames = await collect(streamChat({ question: "hi" }))

    expect(frames).toEqual([
      { event: "token", data: { delta: "a" } },
      { event: "token", data: { delta: "b" } },
    ])
  })

  it("yields an error frame with its detail message", async () => {
    const body = 'event: error\ndata: {"detail":"something broke"}\n\n'

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(sseResponse([body]))

    const frames = await collect(streamChat({ question: "hi" }))

    expect(frames).toEqual([{ event: "error", data: { detail: "something broke" } }])
  })

  it("retries once after a 401 by refreshing the access token", async () => {
    const successBody = 'event: done\ndata: {}\n\n'

    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(sseResponse([successBody]))

    vi.spyOn(client, "refreshAccessToken").mockResolvedValueOnce(true)

    const frames = await collect(streamChat({ question: "hi" }))

    expect(frames).toEqual([{ event: "done", data: {} }])
    expect(fetchSpy).toHaveBeenCalledTimes(2)
  })

  it("throws on a non-ok, non-401 response (e.g. 404 for a bad conversation id)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(null, { status: 404 }))

    await expect(collect(streamChat({ question: "hi", conversation_id: "bad-id" }))).rejects.toThrow(
      /404/,
    )
  })

  it("reassembles frames when the \\n\\n separator itself is split across chunks", async () => {
    // The two bytes of the "\n\n" separator arrive in different chunks —
    // guards against an "optimization" that only scans the newly-arrived
    // chunk instead of the accumulated buffer for the separator.
    const chunk1 = 'event: token\ndata: {"delta":"a"}\n'
    const chunk2 = '\nevent: token\ndata: {"delta":"b"}\n\n'

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(sseResponse([chunk1, chunk2]))

    const frames = await collect(streamChat({ question: "hi" }))

    expect(frames).toEqual([
      { event: "token", data: { delta: "a" } },
      { event: "token", data: { delta: "b" } },
    ])
  })

  it("cancels the underlying stream (not just releases the lock) when the consumer stops early", async () => {
    const body =
      'event: token\ndata: {"delta":"a"}\n\n' + 'event: token\ndata: {"delta":"b"}\n\n'

    const { stream, cancelled } = streamFromChunksTrackingCancel([body])
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    )

    const gen = streamChat({ question: "hi" })
    const first = await gen.next()
    expect(first.value).toEqual({ event: "token", data: { delta: "a" } })

    // Simulate a consumer bailing out early (unmount / navigation / stop
    // button) instead of draining the stream to completion.
    await gen.return(undefined)

    expect(cancelled()).toBe(true)
  })
})
