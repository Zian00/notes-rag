import { useMutation, useQueryClient, type UseMutationResult, type UseQueryResult } from "@tanstack/react-query"
import { $api, fetchClient } from "@/api/client"
import { UploadError } from "@/api/uploadError"
import { DeleteError } from "@/api/deleteError"
import type { components } from "@/api/schema"

type DocumentResponse = components["schemas"]["DocumentResponse"]
type DuplicateDocumentResponse = components["schemas"]["DuplicateDocumentResponse"]

// Derives the exact query key openapi-react-query uses for useDocuments(undefined) — the
// default (unfiltered) course — rather than hardcoding a guessed key, so invalidation can't
// silently drift if the library's key shape changes.
//
// The empty query:{} makes this a deep-partial PREFIX that matches every documents query
// regardless of its course filter (TanStack's partialMatchKey iterates the filter's own
// keys, so an empty query object matches any populated one). Do NOT refactor this to a
// populated filter (e.g. { course: undefined }) — that would stop invalidating
// course-filtered lists.
function getDocumentsListKey() {
  return $api.queryOptions("get", "/documents", { params: { query: {} } }).queryKey
}

/** Input shape for useUploadDocument's mutate() — mirrors the backend's multipart form fields. */
export interface UploadDocumentInput {
  file: File
  title?: string
  course?: string
  tags?: string[]
}

// Lists documents, optionally scoped to a course. Thin wrapper over openapi-react-query
// so callers don't need to know the ("get", "/documents", { params }) call shape.
export function useDocuments(course?: string): UseQueryResult<DocumentResponse[], unknown> {
  return $api.useQuery("get", "/documents", { params: { query: { course } } })
}

// Uploads a document via multipart/form-data and invalidates the documents list on success.
export function useUploadDocument(): UseMutationResult<DocumentResponse, UploadError, UploadDocumentInput> {
  // useQueryClient() (not the app's queryClient singleton import) resolves to whatever
  // QueryClient the nearest QueryClientProvider supplies — the same instance useDocuments()
  // reads from — so invalidation always targets the right cache (tests provide their own
  // QueryClient per render, and this hook must invalidate that one, not a separate singleton).
  const queryClient = useQueryClient()
  const documentsListKey = getDocumentsListKey()

  return useMutation<DocumentResponse, UploadError, UploadDocumentInput>({
    mutationFn: async ({ file, title, course, tags }) => {
      // FastAPI's `list[str] = Form()` expects one repeated "tags" field per tag,
      // not a single comma-joined or JSON-encoded value — FormData.append per tag
      // matches that wire format.
      const formData = new FormData()
      formData.append("file", file)
      if (title) formData.append("title", title)
      if (course) formData.append("course", course)
      tags?.forEach((tag) => formData.append("tags", tag))

      // openapbi-fetch's generated ody type for this operation is the decoded
      // Body_upload_document_documents_post object, not FormData — but a raw FormData
      // is what we actually need to send so the browser can set the multipart
      // boundary itself. bodySerializer: (b) => b passes the FormData through
      // untouched instead of JSON.stringify-ing it; the `as unknown as ...` cast
      // is narrowly scoped to this one call, not a general escape hatch.
      const { data, error, response } = await fetchClient.POST("/documents", {
        body: formData as unknown as components["schemas"]["Body_upload_document_documents_post"],
        bodySerializer: (body) => body as unknown as BodyInit,
      })

      if (error || !data) {
        if (response.status === 409) {
          const duplicate = error as DuplicateDocumentResponse
          throw new UploadError(409, duplicate.detail, duplicate.document_id)
        }
        // 400 (empty/unsupported file) and 413 (too large) aren't declared in the
        // generated schema (FastAPI raises them outside the typed response map),
        // so `error` here is a loosely-typed fallback body — fall back to a
        // generic message rather than assuming its shape.
        const detail =
          error && typeof error === "object" && "detail" in error ? String(error.detail) : "Upload failed"
        throw new UploadError(response.status, detail)
      }

      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsListKey })
    },
  })
}

// Deletes a document by id and invalidates the documents list on success.
export function useDeleteDocument(): UseMutationResult<void, DeleteError, string> {
  const queryClient = useQueryClient()
  const documentsListKey = getDocumentsListKey()

  return useMutation<void, DeleteError, string>({
    mutationFn: async (documentId) => {
      const { error, response } = await fetchClient.DELETE("/documents/{document_id}", {
        params: { path: { document_id: documentId } },
      })
      if (error) {
        throw new DeleteError(response.status, "Failed to delete document")
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsListKey })
    },
  })
}
