import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { http, HttpResponse } from "msw"
import type { ReactNode } from "react"
import { server } from "./msw/server"
import { Toaster } from "@/components/ui/sonner"
import { DocumentsPage } from "@/routes/DocumentsPage"
import type { components } from "@/api/schema"

// client.ts resolves its relative "/api" base against window.location.origin —
// derive the test base the same way so the two can't drift (see tests/client.test.ts).
const API_BASE = `${window.location.origin}/api`

type DocumentResponse = components["schemas"]["DocumentResponse"]

const doc1: DocumentResponse = {
  id: "11111111-1111-1111-1111-111111111111",
  filename: "notes1.pdf",
  title: "Notes 1",
  course: "cs101",
  tags: ["week1"],
  content_type: "application/pdf",
  page_count: 3,
  chunk_count: 10,
  status: "ready",
  error_message: null,
  file_size: 1024,
  embedding_model: "test-model",
  embedding_dimension: 384,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

const doc2: DocumentResponse = {
  ...doc1,
  id: "22222222-2222-2222-2222-222222222222",
  filename: "notes2.pdf",
  title: "Notes 2",
  status: "ready",
  error_message: null,
}

// Renders DocumentsPage under a fresh QueryClient + the real Toaster (sonner) so
// toast text lands in the DOM and can be asserted on directly — the app's own
// <Toaster/> isn't mounted by this isolated render, so it's added explicitly here.
function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return render(
    <Wrapper>
      <DocumentsPage />
      <Toaster />
    </Wrapper>,
  )
}

describe("DocumentsPage", () => {
  it("shows an empty state when there are no documents", async () => {
    server.use(http.get(`${API_BASE}/documents`, () => HttpResponse.json([])))

    renderPage()

    expect(await screen.findByText(/upload your first note/i)).toBeInTheDocument()
  })

  it("renders the document list with metadata", async () => {
    server.use(http.get(`${API_BASE}/documents`, () => HttpResponse.json([doc1, doc2])))

    renderPage()

    expect(await screen.findByText("Notes 1")).toBeInTheDocument()
    expect(screen.getByText("Notes 2")).toBeInTheDocument()
    expect(screen.getAllByText("cs101")).toHaveLength(2)
    expect(screen.getAllByText(/10 chunks/i)).toHaveLength(2)
  })

  it("uploads a file and refetches the list on success", async () => {
    let listCallCount = 0
    server.use(
      http.get(`${API_BASE}/documents`, () => {
        listCallCount += 1
        return HttpResponse.json(listCallCount === 1 ? [] : [doc1])
      }),
      http.post(`${API_BASE}/documents`, () => HttpResponse.json(doc1, { status: 201 })),
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["hello world"], "notes1.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, { selector: "input" }) as HTMLInputElement
    await user.upload(fileInput, file)

    await screen.findByText("notes1.pdf")

    await user.click(screen.getByRole("button", { name: /^upload$/i }))

    expect(await screen.findByText(/uploaded/i)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText("Notes 1")).toBeInTheDocument())
    expect(listCallCount).toBe(2)
  })

  it("does not nest the file input inside a button (guards against click re-entrancy)", async () => {
    server.use(http.get(`${API_BASE}/documents`, () => HttpResponse.json([])))

    renderPage()

    await screen.findByText(/upload your first note/i)

    // The dropzone must be a <label>, not a <button> wrapping interactive content —
    // a button ancestor here would mean clicking it re-triggers input.click() via
    // the synthesized click bubbling back up, which can double-open the picker.
    const fileInput = screen.getByLabelText(/choose file/i, { selector: "input" })
    expect(fileInput.closest("button")).toBeNull()
    expect(fileInput.closest("label")).not.toBeNull()
  })

  it("surfaces a clear message when the upload is too large (413)", async () => {
    server.use(
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([])),
      http.post(`${API_BASE}/documents`, () =>
        HttpResponse.json({ detail: "File too large" }, { status: 413 }),
      ),
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["x"], "big.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, { selector: "input" }) as HTMLInputElement
    await user.upload(fileInput, file)
    await user.click(screen.getByRole("button", { name: /^upload$/i }))

    expect(await screen.findByText(/too large/i)).toBeInTheDocument()
  })

  it("surfaces a clear message on duplicate upload (409)", async () => {
    server.use(
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([])),
      http.post(`${API_BASE}/documents`, () =>
        HttpResponse.json({ detail: "Document already exists", document_id: doc1.id }, { status: 409 }),
      ),
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["x"], "dup.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, { selector: "input" }) as HTMLInputElement
    await user.upload(fileInput, file)
    await user.click(screen.getByRole("button", { name: /^upload$/i }))

    expect(await screen.findByText(/already uploaded/i)).toBeInTheDocument()
  })

  it("deletes a document after confirming in the dialog", async () => {
    let listCallCount = 0
    server.use(
      http.get(`${API_BASE}/documents`, () => {
        listCallCount += 1
        return HttpResponse.json(listCallCount === 1 ? [doc1] : [])
      }),
      http.delete(`${API_BASE}/documents/${doc1.id}`, () => new HttpResponse(null, { status: 204 })),
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText("Notes 1")

    await user.click(screen.getByRole("button", { name: /delete notes 1/i }))

    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: /delete/i }))

    await waitFor(() => expect(screen.queryByText("Notes 1")).not.toBeInTheDocument())
    expect(await screen.findByText(/upload your first note/i)).toBeInTheDocument()
  })
})
