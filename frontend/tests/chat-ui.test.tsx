import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { http, HttpResponse } from "msw"
import type { ReactNode } from "react"
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

const mockUser = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "user@example.com",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
}

const convo1: ConversationResponse = {
  id: "11111111-1111-1111-1111-111111111111",
  title: "First chat",
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
      HttpResponse.json({ access_token: "t", token_type: "bearer" }),
    ),
    http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
    http.get(`${API_BASE}/conversations`, () => HttpResponse.json([])),
    // Catch-all so a new-chat's post-send navigation to /chat/:id (which mounts
    // useConversation for that fresh id) doesn't trip MSW's unhandled-request
    // warning; ChatPage's seed-when-empty guard means this 404 is harmless —
    // the just-streamed live messages are already non-empty by the time this
    // resolves, so there's nothing to seed anyway.
    http.get(`${API_BASE}/conversations/:conversationId`, () =>
      HttpResponse.json({ detail: "Conversation not found" }, { status: 404 }),
    ),
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
      <AuthProvider>
        <MemoryRouter initialEntries={initialEntries}>
          <AppRoutes />
        </MemoryRouter>
        <Toaster />
      </AuthProvider>
    </Wrapper>,
  )
}

afterEach(() => {
  mockStreamChat.mockReset()
})

describe("ChatPage", () => {
  it("sends a question, streams tokens into the assistant bubble, and shows citations on expand", async () => {
    mockAuthed()
    const citation: Citation = { chunk_id: "c1", filename: "notes.pdf", title: "Notes", section: "Intro", score: 0.912 }
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "Hel" } },
        { event: "token", data: { delta: "lo" } },
        { event: "citations", data: [citation] },
        { event: "done", data: {} },
      ]),
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

  it("loads and seeds history when opening an existing conversation", async () => {
    mockAuthed()
    server.use(
      http.get(`${API_BASE}/conversations/${convo1.id}`, () => HttpResponse.json(convo1Detail)),
    )

    renderApp([`/chat/${convo1.id}`])

    expect(await screen.findByText("What is a hash map?")).toBeInTheDocument()
    expect(await screen.findByText("A hash map is a key-value data structure.")).toBeInTheDocument()
  })

  it("new chat clears the thread and navigates to /chat", async () => {
    mockAuthed()
    mockStreamChat.mockImplementation(
      scriptedStream([
        { event: "meta", data: { conversation_id: "convo-new" } },
        { event: "token", data: { delta: "answer" } },
        { event: "done", data: {} },
      ]),
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
      ]),
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
      (_, element) => element?.tagName === "P" && element.textContent === "line one\nline two",
    )
    expect(userBubble).toBeInTheDocument()
  })
})

describe("Sidebar conversation list", () => {
  it("shows conversations, highlights the active one, and deletes on confirm", async () => {
    mockAuthed()
    server.use(
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json([convo1])),
      http.delete(`${API_BASE}/conversations/${convo1.id}`, () => new HttpResponse(null, { status: 204 })),
    )

    const user = userEvent.setup()
    renderApp([`/chat/${convo1.id}`])

    const link = await screen.findByRole("link", { name: /first chat/i })
    expect(link).toHaveAttribute("aria-current", "page")

    await user.click(screen.getByRole("button", { name: /delete first chat/i }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: /delete/i }))

    expect(await screen.findByText(/deleted/i)).toBeInTheDocument()
  })
})
