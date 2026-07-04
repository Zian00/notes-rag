import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { http, HttpResponse } from "msw"
import type { ReactNode } from "react"
import { server } from "./msw/server"
import { useDocuments, useUploadDocument, useDeleteDocument } from "@/api/hooks/useDocuments"
import { UploadError } from "@/api/uploadError"
import { DeleteError } from "@/api/deleteError"
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

describe("useDocuments", () => {
  it("resolves with the documents list", async () => {
    server.use(http.get(`${API_BASE}/documents`, () => HttpResponse.json([doc1, doc2])))

    const { result } = renderHook(() => useDocuments(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.error).toBeNull()
    expect(result.current.data).toEqual([doc1, doc2])
  })

  it("sends the course filter as a query param", async () => {
    let capturedCourse: string | null = null
    server.use(
      http.get(`${API_BASE}/documents`, ({ request }) => {
        capturedCourse = new URL(request.url).searchParams.get("course")
        return HttpResponse.json([doc1])
      }),
    )

    const { result } = renderHook(() => useDocuments("cs101"), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(capturedCourse).toBe("cs101")
    expect(result.current.data).toEqual([doc1])
  })
})

describe("useUploadDocument", () => {
  it("uploads multipart form data and invalidates the documents list on success", async () => {
    let listCallCount = 0
    let capturedContentType: string | null = null
    let capturedFields: { filePresent: boolean; title: FormDataEntryValue | null; tags: FormDataEntryValue[] } = {
      filePresent: false,
      title: null,
      tags: [],
    }

    server.use(
      http.get(`${API_BASE}/documents`, () => {
        listCallCount += 1
        return HttpResponse.json(listCallCount === 1 ? [] : [doc1])
      }),
      http.post(`${API_BASE}/documents`, async ({ request }) => {
        capturedContentType = request.headers.get("Content-Type")
        const formData = await request.formData()
        const file = formData.get("file")
        // MSW's request.formData() parses the body with undici's File/Blob classes,
        // which are a different realm than jsdom's globals here — `instanceof Blob`
        // fails even though the value genuinely is a file part. Duck-type on the
        // constructor name instead of asserting the exact filename (which jsdom's
        // fetch/FormData plumbing doesn't reliably preserve in this test setup).
        capturedFields = {
          filePresent: file?.constructor?.name === "File",
          title: formData.get("title"),
          tags: formData.getAll("tags"),
        }
        return HttpResponse.json(doc1, { status: 201 })
      }),
    )

    // Both hooks are rendered from a single renderHook() call (one React root) — with two
    // separate renderHook() roots, the list query's cache-driven re-render after the upload
    // mutation's invalidateQueries doesn't reliably propagate to the other root's `result.current`
    // snapshot under this test setup, even though the underlying QueryClient cache is correctly
    // updated (confirmed by inspecting queryClient.getQueryCache() directly during triage).
    const { result } = renderHook(
      () => ({ list: useDocuments(), upload: useUploadDocument() }),
      { wrapper: createWrapper() },
    )
    await waitFor(() => expect(result.current.list.isLoading).toBe(false))
    expect(listCallCount).toBe(1)

    const file = new File(["hello world"], "notes1.pdf", { type: "application/pdf" })
    result.current.upload.mutate({ file, title: "Notes 1", tags: ["week1", "week2"] })

    await waitFor(() => expect(result.current.upload.isSuccess).toBe(true))

    // Multipart boundary is browser/undici-generated, so only assert the family, not the exact boundary.
    expect(capturedContentType).toMatch(/^multipart\/form-data/)
    expect(capturedFields.filePresent).toBe(true)
    expect(capturedFields.title).toBe("Notes 1")
    expect(capturedFields.tags).toEqual(["week1", "week2"])

    // Invalidation should trigger a refetch of the documents list query — wait for the
    // refetched data (not just the call count) since the response resolves asynchronously
    // after the second GET fires.
    await waitFor(() => expect(result.current.list.data).toEqual([doc1]))
    expect(listCallCount).toBe(2)
  })

  it("throws UploadError with status 409 and existingDocumentId on duplicate", async () => {
    server.use(
      http.post(`${API_BASE}/documents`, () =>
        HttpResponse.json(
          { detail: "Document already exists", document_id: doc1.id },
          { status: 409 },
        ),
      ),
    )

    const { result } = renderHook(() => useUploadDocument(), { wrapper: createWrapper() })
    const file = new File(["hello"], "dup.pdf", { type: "application/pdf" })

    result.current.mutate({ file })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(UploadError)
    expect((result.current.error as UploadError).status).toBe(409)
    expect((result.current.error as UploadError).existingDocumentId).toBe(doc1.id)
  })

  it("throws UploadError with status 413 when the file is too large", async () => {
    server.use(
      http.post(`${API_BASE}/documents`, () =>
        HttpResponse.json({ detail: "File too large" }, { status: 413 }),
      ),
    )

    const { result } = renderHook(() => useUploadDocument(), { wrapper: createWrapper() })
    const file = new File(["hello"], "big.pdf", { type: "application/pdf" })

    result.current.mutate({ file })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(UploadError)
    expect((result.current.error as UploadError).status).toBe(413)
  })
})

describe("useDeleteDocument", () => {
  it("deletes a document and invalidates the documents list", async () => {
    let listCallCount = 0

    server.use(
      http.get(`${API_BASE}/documents`, () => {
        listCallCount += 1
        return HttpResponse.json(listCallCount === 1 ? [doc1] : [])
      }),
      http.delete(`${API_BASE}/documents/${doc1.id}`, () => new HttpResponse(null, { status: 204 })),
    )

    // Both hooks share a single renderHook() root — see the comment in the upload
    // invalidation test above for why that matters for cross-hook cache observation.
    const { result } = renderHook(
      () => ({ list: useDocuments(), delete: useDeleteDocument() }),
      { wrapper: createWrapper() },
    )
    await waitFor(() => expect(result.current.list.isLoading).toBe(false))
    expect(result.current.list.data).toEqual([doc1])

    result.current.delete.mutate(doc1.id)

    await waitFor(() => expect(result.current.delete.isSuccess).toBe(true))
    // Invalidation should trigger a refetch — wait for the refetched (empty) data,
    // not just the call count, since the response resolves asynchronously.
    await waitFor(() => expect(result.current.list.data).toEqual([]))
    expect(listCallCount).toBe(2)
  })

  it("throws DeleteError with status 404 when the document is already gone", async () => {
    server.use(
      http.delete(`${API_BASE}/documents/${doc1.id}`, () =>
        HttpResponse.json({ detail: "Document not found" }, { status: 404 }),
      ),
    )

    const { result } = renderHook(() => useDeleteDocument(), { wrapper: createWrapper() })

    result.current.mutate(doc1.id)

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(DeleteError)
    expect((result.current.error as DeleteError).status).toBe(404)
  })
})
