import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { http, HttpResponse } from "msw"
import type { ReactNode } from "react"
import { ThemeProvider } from "next-themes"
import { server } from "./msw/server"
import { Toaster } from "@/components/ui/sonner"
import { AuthProvider } from "@/auth/AuthContext"
import { AppRoutes } from "@/AppRoutes"
import type { ChatFrame, Citation } from "@/api/chatStream"
import type { components } from "@/api/schema"

// Isolates the UI from the real SSE/fetch machinery (already covered by
// tests/chatStream.test.ts and tests/useChat.test.tsx) by scripting deterministic
// frame sequences, mirroring the mocking approach in tests/useChat.test.tsx.
vi.mock("@/api/chatStream", () => ({
  streamChat: vi.fn(),
}))

import { streamChat } from "@/api/chatStream"

const mockStreamChat = vi.mocked(streamChat)

function scriptedStream(frames: ChatFrame[]) {
  return async function* (): AsyncGenerator<ChatFrame> {
    for (const frame of frames) {
      yield frame
    }
  }
}

// client.ts resolves its relative "/api" base against window.location.origin —
// derive the test base the same way so the two can't drift (see tests/client.test.ts).
const API_BASE = `${window.location.origin}/api`

type ConversationResponse = components["schemas"]["ConversationResponse"]
type ConversationDetail = components["schemas"]["ConversationDetail"]
type GroupResponse = components["schemas"]["GroupResponse"]
type DocumentResponse = components["schemas"]["DocumentResponse"]

const mockUser = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "user@example.com",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
}

const convo1: ConversationResponse = {
  id: "11111111-1111-1111-1111-111111111111",
  title: "First chat",
  group_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

const convo1Detail: ConversationDetail = {
  ...convo1,
  messages: [
    { role: "user", content: "What is a hash map?" },
    { role: "assistant", content: "A hash map is a key-value data structure." },
  ],
}

// Every test here is an authed user landing inside AppShell — authed via a
// successful silent refresh, mirroring tests/routing.test.tsx's pattern.
function mockAuthed() {
  server.use(
    http.post(`${API_BASE}/auth/refresh`, () =>
      HttpResponse.json({ access_token: "t", token_type: "bearer" })
    ),
    http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
    http.get(`${API_BASE}/conversations`, () => HttpResponse.json([])),
    // Sidebar always renders group sections, so every test needs a default
    // /groups response even when groups aren't what's under test.
    http.get(`${API_BASE}/groups`, () => HttpResponse.json([])),
    // ChatInput's attach chip polls the documents list for status — every test
    // needs a default /documents response even when attach isn't under test.
    http.get(`${API_BASE}/documents`, () => HttpResponse.json([])),
    // Catch-all so a new-chat's post-send navigation to /chat/:id (which mounts
    // useConversation for that fresh id) doesn't trip MSW's unhandled-request
    // warning; ChatPage's seed-when-empty guard means this 404 is harmless —
    // the just-streamed live messages are already non-empty by the time this
    // resolves, so there's nothing to seed anyway.
    http.get(`${API_BASE}/conversations/:conversationId`, () =>
      HttpResponse.json({ detail: "Conversation not found" }, { status: 404 })
    )
  )
}

function renderApp(initialEntries: string[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return render(
    <Wrapper>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <AuthProvider>
          <MemoryRouter initialEntries={initialEntries}>
            <AppRoutes />
          </MemoryRouter>
          <Toaster />
        </AuthProvider>
      </ThemeProvider>
    </Wrapper>
  )
}

afterEach(() => {
  mockStreamChat.mockReset()
})

describe("ChatPage", () => {
  it("sends a question, streams tokens into the assistant bubble, and shows citations on expand", async () => {
    mockAuthed()
    const citation: Citation = {
      chunk_id: "c1",
      filename: "notes.pdf",
      title: "Notes",
      section: "Intro",
      score: 0.912,
    }
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "Hel" } },
        { event: "token", data: { delta: "lo" } },
        { event: "citations", data: [citation] },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "hi there")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    expect(await screen.findByText("hi there")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText("Hello")).toBeInTheDocument())

    const sourcesToggle = await screen.findByRole("button", { name: /sources/i })
    expect(sourcesToggle).toHaveAttribute("aria-expanded", "false")
    await user.click(sourcesToggle)
    expect(sourcesToggle).toHaveAttribute("aria-expanded", "true")
    expect(await screen.findByText(citation.title as string)).toBeInTheDocument()
  })

  it("shows a 'Thinking' indicator in the empty bubble until the first token arrives", async () => {
    mockAuthed()
    // Hold the stream open after `meta` but before any `token`, so the assistant
    // bubble sits in its empty, waiting-for-first-token state — the latency gap
    // the Thinking indicator fills. Same gate pattern as the mid-stream tests.
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    mockStreamChat.mockImplementation(async function* (): AsyncGenerator<ChatFrame> {
      yield { event: "meta", data: { conversation_id: "convo-new" } }
      await gate
      yield { event: "token", data: { delta: "here it is" } }
      yield { event: "done", data: {} }
    })

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "explain N:N relationship")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    // Before any token streams in, the empty bubble reads "Thinking", not blank.
    expect(await screen.findByText(/thinking/i)).toBeInTheDocument()

    // Once the first token arrives the indicator gives way to the real answer.
    release()
    await waitFor(() => expect(screen.getByText("here it is")).toBeInTheDocument())
    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument()
  })

  it("loads and seeds history when opening an existing conversation", async () => {
    mockAuthed()
    server.use(
      http.get(`${API_BASE}/conversations/${convo1.id}`, () => HttpResponse.json(convo1Detail))
    )

    renderApp([`/chat/${convo1.id}`])

    expect(await screen.findByText("What is a hash map?")).toBeInTheDocument()
    expect(await screen.findByText("A hash map is a key-value data structure.")).toBeInTheDocument()
  })

  it("restores persisted citations when reopening a conversation", async () => {
    mockAuthed()
    const citation: Citation = { chunk_id: "c1", filename: "notes.pdf", title: "Notes" }
    server.use(
      http.get(`${API_BASE}/conversations/${convo1.id}`, () =>
        HttpResponse.json({
          ...convo1Detail,
          messages: [
            { role: "user", content: "What is a hash map?" },
            {
              role: "assistant",
              content: "A hash map is a key-value data structure [1].",
              citations: [citation],
            },
          ],
        })
      )
    )

    const user = userEvent.setup()
    renderApp([`/chat/${convo1.id}`])

    // Sources come back from persisted history, not from a live SSE stream.
    const sourcesToggle = await screen.findByRole("button", { name: /sources/i })
    await user.click(sourcesToggle)
    expect(await screen.findByText("Notes")).toBeInTheDocument()
  })

  it("new chat clears the thread and navigates to /chat", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "answer" } },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "question one")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    await screen.findByText("question one")
    await waitFor(() => expect(screen.getByText("answer")).toBeInTheDocument())

    await user.click(screen.getByRole("button", { name: /new chat/i }))

    expect(screen.queryByText("question one")).not.toBeInTheDocument()
    expect(await screen.findByText(/ask something about your notes/i)).toBeInTheDocument()
  })

  it("Enter sends the message; Shift+Enter inserts a newline instead", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "ok" } },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "line one")
    await user.keyboard("{Shift>}{Enter}{/Shift}")
    await user.type(textbox, "line two")

    expect((textbox as HTMLTextAreaElement).value).toBe("line one\nline two")

    await user.keyboard("{Enter}")

    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument())

    // Default text-matcher normalization collapses whitespace (including the
    // embedded newline this bubble intentionally preserves via
    // whitespace-pre-wrap) — matched here directly on the <p> so the search
    // isn't also satisfied by ancestor elements sharing the same textContent.
    const userBubble = screen.getByText(
      (_, element) => element?.tagName === "P" && element.textContent === "line one\nline two"
    )
    expect(userBubble).toBeInTheDocument()
  })

  it("allows typing while streaming but blocks Enter from sending until it finishes", async () => {
    mockAuthed()
    // Never resolves on its own — the test controls when "done" fires via `release`,
    // so streaming stays in progress while asserting Enter is blocked mid-stream.
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    mockStreamChat.mockImplementation(async function* (): AsyncGenerator<ChatFrame> {
      yield { event: "meta", data: { conversation_id: "convo-new" } }
      yield { event: "token", data: { delta: "partial" } }
      await gate
      yield { event: "done", data: {} }
    })

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "first question")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    // Streaming is now in progress — Stop replaces Send, and the textarea must
    // stay editable (Task 13 item 2) even though sending itself is blocked.
    expect(await screen.findByRole("button", { name: /^stop$/i })).toBeInTheDocument()
    expect(textbox).not.toBeDisabled()

    await user.type(textbox, "typed mid-stream")
    expect((textbox as HTMLTextAreaElement).value).toBe("typed mid-stream")

    // Enter must not call onSend while isStreaming is true — mockStreamChat is
    // only ever invoked once for this test if the guard holds.
    await user.keyboard("{Enter}")
    expect(mockStreamChat).toHaveBeenCalledTimes(1)

    release()
    await waitFor(() => expect(screen.getByRole("button", { name: /^send$/i })).toBeInTheDocument())
  })

  it("new chat while already at bare /chat clears a lingering thread", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([{ event: "error", data: { detail: "boom" } }])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "question that errors")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    // The mocked stream never sends a `meta` frame, so onConversationCreated
    // never fires and the URL stays at bare /chat — this is the "lingering
    // thread from a pre-meta error" scenario Task 13 item 5 targets.
    await screen.findByText("question that errors")

    await user.click(screen.getByRole("button", { name: /new chat/i }))

    expect(screen.queryByText("question that errors")).not.toBeInTheDocument()
    expect(await screen.findByText(/ask something about your notes/i)).toBeInTheDocument()
  })
})

describe("Markdown rendering", () => {
  it("renders assistant markdown as real elements, not literal syntax", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        {
          event: "token",
          data: { delta: "**bold**\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |" },
        },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "format please")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    // The typewriter reveal drains progressively, so wait for the LAST piece of
    // content to appear (proves the full string has revealed) before asserting
    // on earlier pieces synchronously — revealing is monotonic/append-only.
    expect(await screen.findByRole("table")).toBeInTheDocument()
    expect(screen.getByText("bold").tagName).toBe("STRONG")
    expect(screen.getByText("one").tagName).toBe("LI")
  })

  it("never markdown-renders the user's own message", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "ok" } },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "**not bold** literally")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    const userBubble = await screen.findByText("**not bold** literally")
    expect(userBubble.tagName).toBe("P")
  })

  it("never markdown-renders an error message", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "error", data: { detail: "**request failed**: see logs" } },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "what is a heap?")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    const errorBubble = await screen.findByText("**request failed**: see logs")
    expect(errorBubble.tagName).toBe("P")
  })

  it("never auto-renders assistant markdown images", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "![a diagram](https://example.com/x.png)" } },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "show me")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    await screen.findByText(/a diagram/i)
    expect(screen.queryByRole("img")).not.toBeInTheDocument()
  })

  it("assistant links open safely in a new tab", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "[my notes](https://example.com/notes)" } },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "link please")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    // Default 1000ms timeout is too tight here: the typewriter reveal drains
    // this delta a few characters per tick, and a full app re-render (sidebar,
    // markdown re-parse) per tick costs more than the tick interval itself in
    // this test environment — give it real margin rather than risk flakiness.
    const link = await screen.findByRole("link", { name: /my notes/i }, { timeout: 3000 })
    expect(link).toHaveAttribute("target", "_blank")
    expect(link).toHaveAttribute("rel", "noopener noreferrer")
  })

  it("places the streaming cursor after the true last text node, even inside a list", async () => {
    mockAuthed()
    // Never resolves on its own — mid-stream cursor placement only matters
    // while streaming is still in progress, same pattern as the Enter-blocking test.
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    mockStreamChat.mockImplementation(async function* (): AsyncGenerator<ChatFrame> {
      yield { event: "meta", data: { conversation_id: "convo-new" } }
      yield { event: "token", data: { delta: "- first\n- second" } }
      await gate
      yield { event: "done", data: {} }
    })

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "a list please")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    // The cursor must land inside the last <li>, right after its text — not
    // trailing the whole message in its own block.
    await waitFor(() => {
      const secondItem = screen.getByText("second")
      expect(secondItem.tagName).toBe("LI")
      expect(secondItem.querySelector('[aria-hidden="true"]')).toBeInTheDocument()
    })

    release()
    await waitFor(() => {
      const secondItem = screen.getByText("second")
      expect(secondItem.querySelector('[aria-hidden="true"]')).not.toBeInTheDocument()
    })
  })

  it("clicking a citation marker expands and highlights the matching source", async () => {
    mockAuthed()
    const citation: Citation = { chunk_id: "c1", filename: "notes.pdf", title: "Notes" }
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "A heap is a tree [1]." } },
        { event: "citations", data: [citation] },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "what is a heap?")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    const marker = await screen.findByRole("button", { name: "[1]" })
    expect(screen.getByRole("button", { name: /sources/i })).toHaveAttribute(
      "aria-expanded",
      "false"
    )

    // fireEvent (not userEvent) deliberately: userEvent's pointer-events hit-test
    // heuristic checks document.elementFromPoint() at the target's computed
    // bounding rect, which jsdom (no real layout engine) returns as zero-sized
    // for a small inline element this deeply nested in ReactMarkdown's output —
    // causing userEvent to silently skip the click even though the handler is
    // wired correctly (fireEvent, a raw DOM dispatch bypassing that heuristic,
    // confirms the click handler itself works). Still worth checking in a real
    // browser before considering this feature done.
    fireEvent.click(marker)

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sources/i })).toHaveAttribute(
        "aria-expanded",
        "true"
      )
    )
    expect(await screen.findByText("Notes")).toBeInTheDocument()
  })

  it("leaves an out-of-range citation marker as plain, unclickable text", async () => {
    mockAuthed()
    const citation: Citation = { chunk_id: "c1", filename: "notes.pdf", title: "Notes" }
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        // Only 1 citation is sent, but the model (miscounting) wrote [2].
        { event: "token", data: { delta: "A heap is a tree [2]." } },
        { event: "citations", data: [citation] },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "what is a heap?")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    await waitFor(() => {
      expect(screen.getByText("A heap is a tree [2].")).toBeInTheDocument()
      expect(screen.queryByRole("button", { name: "[2]" })).not.toBeInTheDocument()
    })
  })
})

describe("AppShell mobile drawer", () => {
  it("closes on Escape", async () => {
    mockAuthed()
    const user = userEvent.setup()
    renderApp(["/chat"])

    await screen.findByText(/ask something about your notes/i)

    const openButton = screen.getByRole("button", { name: /open navigation/i })
    await user.click(openButton)
    // Two "Close navigation" controls exist while the drawer is open (the full-
    // screen backdrop button and the header toggle) — getAllBy* is deliberate.
    expect(await screen.findAllByRole("button", { name: /close navigation/i })).toHaveLength(2)

    await user.keyboard("{Escape}")

    expect(screen.queryAllByRole("button", { name: /close navigation/i })).toHaveLength(0)
    expect(screen.getByRole("button", { name: /open navigation/i })).toBeInTheDocument()
  })
})

describe("ChatInput top_k clamp", () => {
  it("clamps an out-of-range top_k before sending", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    await screen.findByText(/ask something about your notes/i)

    await user.click(screen.getByRole("button", { name: /filters/i }))
    const topKInput = screen.getByLabelText(/top k/i)
    await user.clear(topKInput)
    await user.type(topKInput, "999")

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "clamp check")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    await waitFor(() => expect(mockStreamChat).toHaveBeenCalledTimes(1))
    const [requestBody] = mockStreamChat.mock.calls[0]
    expect(requestBody.top_k).toBe(20)
  })
})

describe("Theme toggle", () => {
  it("renders in the Sidebar footer and switching to dark applies the .dark class", async () => {
    mockAuthed()
    const user = userEvent.setup()
    renderApp(["/chat"])

    await screen.findByText(/ask something about your notes/i)

    const trigger = screen.getByRole("button", { name: /theme:/i })
    await user.click(trigger)
    await user.click(await screen.findByRole("menuitem", { name: /^dark$/i }))

    await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(true))

    await user.click(screen.getByRole("button", { name: /theme:/i }))
    await user.click(await screen.findByRole("menuitem", { name: /^light$/i }))

    await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(false))
  })
})

describe("Sidebar conversation list", () => {
  it("shows conversations, highlights the active one, and deletes on confirm", async () => {
    mockAuthed()
    server.use(
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json([convo1])),
      http.delete(
        `${API_BASE}/conversations/${convo1.id}`,
        () => new HttpResponse(null, { status: 204 })
      )
    )

    const user = userEvent.setup()
    renderApp([`/chat/${convo1.id}`])

    const link = await screen.findByRole("link", { name: /first chat/i })
    expect(link).toHaveAttribute("aria-current", "page")

    await user.click(screen.getByRole("button", { name: /first chat options/i }))
    await user.click(await screen.findByRole("menuitem", { name: /^delete$/i }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: /^delete$/i }))

    expect(await screen.findByText(/deleted/i)).toBeInTheDocument()
  })
})

describe("Sidebar groups", () => {
  const group1: GroupResponse = {
    id: "33333333-3333-3333-3333-333333333333",
    name: "CS101",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }

  const groupedConvo: ConversationResponse = {
    ...convo1,
    id: "44444444-4444-4444-4444-444444444444",
    title: "Grouped chat",
    group_id: group1.id,
  }

  it("renders chats under their group section and Ungrouped, and collapses a section", async () => {
    mockAuthed()
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json([group1])),
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json([groupedConvo, convo1]))
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const groupHeader = await screen.findByRole("button", { name: "CS101" })
    const ungroupedHeader = screen.getByRole("button", { name: "Ungrouped" })
    expect(screen.getByRole("link", { name: /grouped chat/i })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /first chat/i })).toBeInTheDocument()

    expect(groupHeader).toHaveAttribute("aria-expanded", "true")
    await user.click(groupHeader)
    expect(groupHeader).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("link", { name: /grouped chat/i })).not.toBeInTheDocument()
    // The other section is untouched by collapsing this one.
    expect(ungroupedHeader).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("link", { name: /first chat/i })).toBeInTheDocument()
  })

  it("creates a new group via the inline form", async () => {
    mockAuthed()
    // GET /groups must reflect the just-created group on refetch (useCreateGroup's
    // onSuccess invalidates the list) — a static response wouldn't show the update.
    let groupsState: GroupResponse[] = []
    let createdBody: unknown
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json(groupsState)),
      http.post(`${API_BASE}/groups`, async ({ request }) => {
        createdBody = await request.json()
        groupsState = [...groupsState, group1]
        return HttpResponse.json(group1, { status: 200 })
      })
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    await user.click(await screen.findByRole("button", { name: /new group/i }))
    const input = screen.getByPlaceholderText(/group name/i)
    await user.type(input, "CS101")
    await user.keyboard("{Enter}")

    await waitFor(() => expect(createdBody).toEqual({ name: "CS101" }))
    expect(await screen.findByRole("button", { name: "CS101" })).toBeInTheDocument()
  })

  it("renames a group inline", async () => {
    mockAuthed()
    // Same rationale as the create test — the post-rename refetch needs the
    // renamed group, not a frozen initial snapshot.
    let groupsState: GroupResponse[] = [group1]
    let renamedBody: unknown
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json(groupsState)),
      http.patch(`${API_BASE}/groups/${group1.id}`, async ({ request }) => {
        renamedBody = await request.json()
        const renamed = { ...group1, name: "CS102" }
        groupsState = [renamed]
        return HttpResponse.json(renamed)
      })
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    await user.click(await screen.findByRole("button", { name: `${group1.name} options` }))
    await user.click(await screen.findByRole("menuitem", { name: /rename/i }))

    const input = screen.getByDisplayValue(group1.name)
    await user.clear(input)
    await user.type(input, "CS102")
    await user.keyboard("{Enter}")

    await waitFor(() => expect(renamedBody).toEqual({ name: "CS102" }))
    expect(await screen.findByRole("button", { name: "CS102" })).toBeInTheDocument()
  })

  it("deletes a group and reports the orphan counts", async () => {
    mockAuthed()
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json([group1])),
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json([groupedConvo])),
      http.delete(`${API_BASE}/groups/${group1.id}`, () =>
        HttpResponse.json({ chats_ungrouped: 1, documents_ungrouped: 2 })
      )
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    await user.click(await screen.findByRole("button", { name: `${group1.name} options` }))
    await user.click(await screen.findByRole("menuitem", { name: /delete/i }))

    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: /^delete$/i }))

    expect(await screen.findByText(/1 chat\(s\) and 2 document\(s\)/i)).toBeInTheDocument()
  })

  it("starting a new chat from within a group section carries that group's id on the first send", async () => {
    mockAuthed()
    let capturedGroupId: string | null | undefined
    server.use(http.get(`${API_BASE}/groups`, () => HttpResponse.json([group1])))
    mockStreamChat.mockImplementation((body) => {
      capturedGroupId = body.group_id
      return scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "ok" } },
        { event: "done", data: {} },
      ])()
    })

    const user = userEvent.setup()
    renderApp(["/chat"])

    await user.click(await screen.findByRole("button", { name: `Start chat in ${group1.name}` }))

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "scoped question")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    await waitFor(() => expect(capturedGroupId).toBe(group1.id))

    // A follow-up turn in the same (now-existing) conversation must NOT resend
    // the group id — the backend only honors it at conversation creation.
    await user.type(textbox, "follow up")
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await waitFor(() => expect(capturedGroupId).toBeUndefined())
  })

  it("renames a chat inline from the row menu", async () => {
    mockAuthed()
    // GET /conversations must reflect the rename on the post-mutation refetch —
    // a static response would keep showing the pre-rename title.
    let conversationsState: ConversationResponse[] = [convo1]
    let patchedBody: unknown
    server.use(
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json(conversationsState)),
      http.patch(`${API_BASE}/conversations/${convo1.id}`, async ({ request }) => {
        patchedBody = await request.json()
        const renamed = { ...convo1, title: "Renamed chat" }
        conversationsState = [renamed]
        return HttpResponse.json(renamed)
      })
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    await user.click(await screen.findByRole("button", { name: /first chat options/i }))
    await user.click(await screen.findByRole("menuitem", { name: /^rename$/i }))

    const input = screen.getByDisplayValue(convo1.title as string)
    await user.clear(input)
    await user.type(input, "Renamed chat")
    await user.keyboard("{Enter}")

    await waitFor(() => expect(patchedBody).toEqual({ title: "Renamed chat" }))
    expect(await screen.findByRole("link", { name: /renamed chat/i })).toBeInTheDocument()
  })

  it("moves a chat into a group via the row menu, relocating it into that section", async () => {
    mockAuthed()
    let conversationsState: ConversationResponse[] = [convo1]
    let patchedBody: unknown
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json([group1])),
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json(conversationsState)),
      http.patch(`${API_BASE}/conversations/${convo1.id}`, async ({ request }) => {
        patchedBody = await request.json()
        conversationsState = [{ ...convo1, group_id: group1.id }]
        return HttpResponse.json(conversationsState[0])
      })
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    // Before the move: collapsing Ungrouped hides the chat, proving it's
    // rendered there (and nowhere else) to start with.
    await screen.findByRole("link", { name: /first chat/i })
    await user.click(screen.getByRole("button", { name: "Ungrouped" }))
    expect(screen.queryByRole("link", { name: /first chat/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Ungrouped" }))
    await screen.findByRole("link", { name: /first chat/i })

    await user.click(screen.getByRole("button", { name: /first chat options/i }))
    await user.click(await screen.findByRole("menuitem", { name: group1.name }))

    await waitFor(() => expect(patchedBody).toEqual({ group_id: group1.id }))
    await screen.findByRole("link", { name: /first chat/i })

    // After the move: collapsing the group section hides it, and Ungrouped
    // is empty — the chat relocated, it didn't just get duplicated.
    await user.click(screen.getByRole("button", { name: group1.name }))
    expect(screen.queryByRole("link", { name: /first chat/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: group1.name }))
    await screen.findByRole("link", { name: /first chat/i })
    expect(screen.getAllByText(/no chats here yet/i)).toHaveLength(1)
  })
})

describe("Chat attach", () => {
  const attachGroup: GroupResponse = {
    id: "55555555-5555-5555-5555-555555555555",
    name: "BIO",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }

  const groupedConvo: ConversationResponse = {
    ...convo1,
    id: "66666666-6666-6666-6666-666666666666",
    group_id: attachGroup.id,
  }

  const groupedConvoDetail: ConversationDetail = { ...groupedConvo, messages: [] }

  const uploadedDoc: DocumentResponse = {
    id: "77777777-7777-7777-7777-777777777777",
    filename: "notes.pdf",
    title: null,
    group_id: null,
    tags: [],
    content_type: "application/pdf",
    page_count: null,
    chunk_count: 0,
    status: "pending",
    error_message: null,
    file_size: 1024,
    embedding_model: "test-model",
    embedding_dimension: 384,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }

  function selectAttachFile(name = "notes.pdf") {
    return new File(["hello"], name, { type: "application/pdf" })
  }

  it("uploads immediately on attach, into the ungrouped chat's (null) group, before Send is pressed", async () => {
    mockAuthed()
    let uploadedGroupId: string | undefined | null
    server.use(
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([uploadedDoc])),
      http.post(`${API_BASE}/documents`, async ({ request }) => {
        const form = await request.formData()
        uploadedGroupId = form.get("group_id") as string | null
        return HttpResponse.json(uploadedDoc, { status: 201 })
      })
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const fileInput = await screen.findByLabelText(/attach a file/i, { selector: "input" })
    await user.upload(fileInput, selectAttachFile())

    // Uploaded (POST fired) even though nothing was ever sent.
    await waitFor(() => expect(uploadedGroupId).toBeNull())
    expect(await screen.findByText("notes.pdf")).toBeInTheDocument()
  })

  it("uploads into an existing grouped conversation's group with no prompt", async () => {
    mockAuthed()
    let uploadedGroupId: string | undefined | null
    server.use(
      http.get(`${API_BASE}/conversations/${groupedConvo.id}`, () =>
        HttpResponse.json(groupedConvoDetail)
      ),
      http.post(`${API_BASE}/documents`, async ({ request }) => {
        const form = await request.formData()
        uploadedGroupId = form.get("group_id") as string | null
        return HttpResponse.json({ ...uploadedDoc, group_id: attachGroup.id }, { status: 201 })
      })
    )

    const user = userEvent.setup()
    renderApp([`/chat/${groupedConvo.id}`])

    const fileInput = await screen.findByLabelText(/attach a file/i, { selector: "input" })
    await user.upload(fileInput, selectAttachFile())

    await waitFor(() => expect(uploadedGroupId).toBe(attachGroup.id))
  })

  it("shows an uploading chip that reflects status, and dismisses without deleting the document", async () => {
    mockAuthed()
    let documentsState: DocumentResponse[] = []
    let deleteWasCalled = false
    server.use(
      http.get(`${API_BASE}/documents`, () => HttpResponse.json(documentsState)),
      http.post(`${API_BASE}/documents`, () => {
        documentsState = [uploadedDoc]
        return HttpResponse.json(uploadedDoc, { status: 201 })
      }),
      http.delete(`${API_BASE}/documents/${uploadedDoc.id}`, () => {
        deleteWasCalled = true
        return new HttpResponse(null, { status: 204 })
      })
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const fileInput = await screen.findByLabelText(/attach a file/i, { selector: "input" })
    await user.upload(fileInput, selectAttachFile())

    expect(await screen.findByText("notes.pdf")).toBeInTheDocument()
    expect(screen.getByText(/processing/i)).toBeInTheDocument()

    // The list now reports it ready — the chip's polled status follows.
    // (useDocuments polls every 2s while anything is pending/processing, so
    // this needs longer than the default waitFor timeout to observe.)
    documentsState = [{ ...uploadedDoc, status: "ready" }]
    await waitFor(() => expect(screen.queryByText(/processing/i)).not.toBeInTheDocument(), {
      timeout: 4000,
    })

    await user.click(screen.getByRole("button", { name: /remove attached file/i }))
    expect(screen.queryByText("notes.pdf")).not.toBeInTheDocument()
    expect(deleteWasCalled).toBe(false)
  })

  it("surfaces an upload error immediately without leaving a chip behind", async () => {
    mockAuthed()
    server.use(
      http.post(`${API_BASE}/documents`, () =>
        HttpResponse.json({ detail: "File too large" }, { status: 413 })
      )
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const fileInput = await screen.findByLabelText(/attach a file/i, { selector: "input" })
    await user.upload(fileInput, selectAttachFile())

    expect(await screen.findByText(/too large/i)).toBeInTheDocument()
    expect(screen.queryByText("notes.pdf")).not.toBeInTheDocument()
  })

  it("does not block sending a message on the attached document's processing status", async () => {
    mockAuthed()
    server.use(
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([uploadedDoc])),
      http.post(`${API_BASE}/documents`, () => HttpResponse.json(uploadedDoc, { status: 201 }))
    )
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "ok" } },
        { event: "done", data: {} },
      ])
    )

    const user = userEvent.setup()
    renderApp(["/chat"])

    const fileInput = await screen.findByLabelText(/attach a file/i, { selector: "input" })
    await user.upload(fileInput, selectAttachFile())
    await screen.findByText("notes.pdf")
    expect(screen.getByText(/processing/i)).toBeInTheDocument()

    const textbox = await screen.findByRole("textbox", { name: /message/i })
    await user.type(textbox, "question while attaching")
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    expect(await screen.findByText("question while attaching")).toBeInTheDocument()
    // Still processing — sending didn't wait for the attachment to finish.
    expect(screen.getByText(/processing/i)).toBeInTheDocument()
  })
})
