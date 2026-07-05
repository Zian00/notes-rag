import { API_BASE, getAccessToken, notifyAuthFailure, refreshAccessToken } from "./client"
import type { components } from "./schema"

// The generated OpenAPI schema doesn't surface a `Citation` schema (the
// streaming /chat response isn't a typed JSON body FastAPI can document), so
// it's defined locally to match the backend's dataclass. All fields are
// optional/nullable per the backend contract.
export type Citation = {
  chunk_id?: string | null
  document_id?: string | null
  filename?: string | null
  title?: string | null
  page_number?: number | null
  section?: string | null
  score?: number | null
}

export type ChatFrame =
  | { event: "meta"; data: { conversation_id: string } }
  | { event: "token"; data: { delta: string } }
  | { event: "citations"; data: Citation[] }
  | { event: "done"; data: Record<string, never> }
  | { event: "error"; data: { detail: string } }

// Reuse the generated request body type so this stays in sync with the
// backend's Pydantic model instead of drifting from a hand-written copy.
export type ChatRequestBody = components["schemas"]["ChatRequest"]

// The backend streams the chat answer as Server-Sent Events over POST (a
// grounded answer needs a request body — question, conversation id, filters —
// which a GET-only `EventSource` can't send), so this reads the raw
// `fetch` response body as a stream and parses SSE frames by hand.
export async function* streamChat(
  body: ChatRequestBody,
  signal?: AbortSignal,
): AsyncGenerator<ChatFrame> {
  let res = await postChat(body, signal)

  // Access tokens are short-lived; a mid-flow expiry surfaces as a 401 here
  // just like any other authenticated endpoint. One silent-refresh retry
  // mirrors the openapi-fetch auth middleware's behavior for regular calls.
  if (res.status === 401) {
    const refreshed = await refreshAccessToken()
    if (refreshed) {
      res = await postChat(body, signal)
    } else {
      // Refresh failed (refresh cookie missing/expired) — session is
      // unrecoverable. Mirror the openapi-fetch middleware's behavior
      // (client.ts) so chatStream's 401 path clears the session + redirects
      // to /login instead of leaving the user "authed" with a dead session.
      notifyAuthFailure()
    }
  }

  if (!res.ok || !res.body) {
    throw new Error(`chat request failed: ${res.status}`)
  }

  yield* parseSseStream(res.body)
}

function postChat(body: ChatRequestBody, signal?: AbortSignal): Promise<Response> {
  const accessToken = getAccessToken()
  return fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    credentials: "include",
    body: JSON.stringify(body),
    signal,
  })
}

const FRAME_SEPARATOR = "\n\n"

async function* parseSseStream(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatFrame> {
  const reader = body.getReader()
  // `{ stream: true }` keeps a partial multi-byte UTF-8 sequence buffered
  // inside the decoder across calls instead of emitting a replacement
  // character for a character split across chunk boundaries.
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // A frame isn't necessarily complete when it arrives — chunk
      // boundaries don't align with SSE frame boundaries — so buffer
      // until a full "\n\n"-terminated frame is available before parsing.
      let separatorIndex: number
      while ((separatorIndex = buffer.indexOf(FRAME_SEPARATOR)) !== -1) {
        const rawFrame = buffer.slice(0, separatorIndex)
        buffer = buffer.slice(separatorIndex + FRAME_SEPARATOR.length)

        const frame = parseFrame(rawFrame)
        if (frame) yield frame
      }
    }
  } finally {
    // cancel() (not just releaseLock) tears down the underlying network stream when
    // the consumer stops early (unmount / navigation / stop) so the connection
    // doesn't stay open receiving bytes nobody reads. It also releases the lock.
    await reader.cancel().catch(() => {})
  }
  // Any leftover `buffer` here is an incomplete trailing frame (stream ended
  // without a final blank-line terminator) — intentionally dropped rather
  // than guessed at.
  //
  // No final no-arg `decoder.decode()` flush is needed either: frames end on
  // ASCII "\n\n", so a complete frame is always parsed before end-of-stream;
  // only an already-dropped incomplete trailing frame could hold a split
  // multibyte tail, and that case is discarded above anyway.
}

// Coupled to the backend's exact SSE emitter format: a single "event: " line
// and a single "data: " line, each with exactly one space after the colon.
// A `data:`-without-space line or a frame with multiple `data:` lines (both
// valid per the SSE spec) would be silently skipped here — if the backend
// emitter ever changes its formatting, update this parser to match.
function parseFrame(rawFrame: string): ChatFrame | null {
  let event: string | null = null
  let data: string | null = null

  for (const line of rawFrame.split("\n")) {
    if (line.startsWith("event: ")) {
      event = line.slice("event: ".length).trim()
    } else if (line.startsWith("data: ")) {
      data = line.slice("data: ".length).trim()
    }
  }

  // Skip rather than throw: a malformed frame shouldn't take down an
  // otherwise-good stream (e.g. a stray keep-alive comment line).
  if (!event || data === null) return null

  try {
    return { event, data: JSON.parse(data) } as ChatFrame
  } catch {
    return null
  }
}
