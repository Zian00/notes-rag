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
type GroupResponse = components["schemas"]["GroupResponse"]

const group1: GroupResponse = {
  id: "33333333-3333-3333-3333-333333333333",
  name: "CS101",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

const group2: GroupResponse = {
  ...group1,
  id: "44444444-4444-4444-4444-444444444444",
  name: "CS102",
}

const doc1: DocumentResponse = {
  id: "11111111-1111-1111-1111-111111111111",
  filename: "notes1.pdf",
  title: "Notes 1",
  group_id: "33333333-3333-3333-3333-333333333333",
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
    </Wrapper>
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
    expect(screen.getAllByText(/10 chunks/i)).toHaveLength(2)
  })

  it("uploads a file and refetches the list on success", async () => {
    let listCallCount = 0
    server.use(
      http.get(`${API_BASE}/documents`, () => {
        listCallCount += 1
        return HttpResponse.json(listCallCount === 1 ? [] : [doc1])
      }),
      http.post(`${API_BASE}/documents`, () => HttpResponse.json(doc1, { status: 201 }))
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["hello world"], "notes1.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, {
      selector: "input",
    }) as HTMLInputElement
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
        HttpResponse.json({ detail: "File too large" }, { status: 413 })
      )
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["x"], "big.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, {
      selector: "input",
    }) as HTMLInputElement
    await user.upload(fileInput, file)
    await user.click(screen.getByRole("button", { name: /^upload$/i }))

    expect(await screen.findByText(/too large/i)).toBeInTheDocument()
  })

  it("surfaces a clear message on duplicate upload (409)", async () => {
    server.use(
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([])),
      http.post(`${API_BASE}/documents`, () =>
        HttpResponse.json(
          { detail: "Document already exists", document_id: doc1.id },
          { status: 409 }
        )
      )
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["x"], "dup.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, {
      selector: "input",
    }) as HTMLInputElement
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
      http.delete(`${API_BASE}/documents/${doc1.id}`, () => new HttpResponse(null, { status: 204 }))
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

  it("uploads a file with the selected group", async () => {
    let uploadedFormFields: string[] = []
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json([group1, group2])),
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([])),
      http.post(`${API_BASE}/documents`, async ({ request }) => {
        const form = await request.formData()
        uploadedFormFields = [String(form.get("group_id"))]
        return HttpResponse.json(doc1, { status: 201 })
      })
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["hello world"], "notes1.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, {
      selector: "input",
    }) as HTMLInputElement
    await user.upload(fileInput, file)
    await screen.findByText("notes1.pdf")

    await user.selectOptions(screen.getByLabelText("Group"), group2.name)
    await user.click(screen.getByRole("button", { name: /^upload$/i }))

    await waitFor(() => expect(uploadedFormFields).toEqual([group2.id]))
  })

  it("creates a new group inline from the upload form and assigns it", async () => {
    let uploadedGroupId: string | null = null
    // GET /groups must reflect the just-created group on the post-create refetch
    // (useCreateGroup's onSuccess invalidates the list) — a static [] response
    // would never grow an <option> for it, so the <select>'s value could never
    // resolve to the new group's id.
    let groupsState: GroupResponse[] = []
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json(groupsState)),
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([])),
      http.post(`${API_BASE}/groups`, () => {
        groupsState = [...groupsState, group1]
        return HttpResponse.json(group1, { status: 200 })
      }),
      http.post(`${API_BASE}/documents`, async ({ request }) => {
        const form = await request.formData()
        uploadedGroupId = form.get("group_id") as string | null
        return HttpResponse.json(doc1, { status: 201 })
      })
    )

    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/upload your first note/i)

    const file = new File(["hello world"], "notes1.pdf", { type: "application/pdf" })
    const fileInput = screen.getByLabelText(/choose file/i, {
      selector: "input",
    }) as HTMLInputElement
    await user.upload(fileInput, file)
    await screen.findByText("notes1.pdf")

    await user.selectOptions(screen.getByLabelText("Group"), "+ New group…")
    const newGroupInput = await screen.findByPlaceholderText(/group name/i)
    await user.type(newGroupInput, group1.name)
    await user.keyboard("{Enter}")

    // The select swaps back once the new group resolves, now showing it selected.
    await waitFor(() => expect(screen.getByLabelText("Group")).toHaveValue(group1.id))

    await user.click(screen.getByRole("button", { name: /^upload$/i }))

    await waitFor(() => expect(uploadedGroupId).toBe(group1.id))
  })

  it("changes an existing document's group from its row", async () => {
    let patchedBody: unknown
    server.use(
      http.get(`${API_BASE}/groups`, () => HttpResponse.json([group1, group2])),
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([doc1])),
      http.patch(`${API_BASE}/documents/${doc1.id}`, async ({ request }) => {
        patchedBody = await request.json()
        return HttpResponse.json({ ...doc1, group_id: group2.id })
      })
    )

    renderPage()
    await screen.findByText("Notes 1")

    const user = userEvent.setup()
    const rowGroupSelect = screen.getByLabelText(`Group for ${doc1.title as string}`)
    await user.selectOptions(rowGroupSelect, group2.name)

    await waitFor(() => expect(patchedBody).toEqual({ group_id: group2.id }))
  })
})
