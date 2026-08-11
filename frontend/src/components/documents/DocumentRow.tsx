import { useState } from "react"
import { Loader2, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  useDeleteDocument,
  useReplaceDocument,
  useUpdateDocumentMetadata,
} from "@/api/hooks/useDocuments"
import { DeleteError } from "@/api/deleteError"
import { formatDate, formatFileSize } from "@/lib/format"
import { GroupSelect } from "@/components/documents/GroupSelect"
import type { components } from "@/api/schema"

type DocumentResponse = components["schemas"]["DocumentResponse"]

interface DocumentRowProps {
  document: DocumentResponse
}

export function DocumentRow({ document }: DocumentRowProps) {
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const deleteDocument = useDeleteDocument()
  const replaceDocument = useReplaceDocument()
  const updateMetadata = useUpdateDocumentMetadata()

  const displayName = document.title ?? document.filename
  // Replace/Delete are rejected server-side (409) while a document is
  // pending/processing — disabling them here avoids the round-trip and the
  // confusing error toast that would otherwise follow.
  const isBusy = document.status === "pending" || document.status === "processing"

  async function handleGroupChange(groupId: string | null) {
    try {
      await updateMetadata.mutateAsync({ documentId: document.id, groupId })
    } catch {
      toast.error("Failed to update the document's group.")
    }
  }

  function handleReplaceFileSelected(file: File) {
    replaceDocument.mutate(
      { documentId: document.id, file },
      {
        onSuccess: (result) => {
          toast.success(result.no_changes ? "No changes detected" : "Replacing document…")
        },
        onError: (error) => {
          toast.error(error.message)
        },
      }
    )
  }

  async function handleConfirmDelete() {
    try {
      await deleteDocument.mutateAsync(document.id)
      toast.success("Document deleted.")
      setIsConfirmOpen(false)
    } catch (error) {
      // A 404 means the document is already gone (e.g. deleted elsewhere) —
      // that's not a failure from the user's perspective, so it gets an info
      // toast instead of an alarming error, and the list still refetches.
      if (error instanceof DeleteError && error.status === 404) {
        toast.info("This document was already removed.")
        setIsConfirmOpen(false)
        return
      }
      toast.error("Failed to delete document. Please try again.")
    }
  }

  return (
    <li className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3 transition-colors hover:bg-accent/40">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{displayName}</p>
        {document.status !== "ready" && (
          <p
            className={
              document.status === "failed"
                ? "mt-0.5 flex items-center gap-1.5 text-sm text-destructive"
                : "mt-0.5 flex items-center gap-1.5 text-sm text-muted-foreground"
            }
          >
            {document.status !== "failed" && (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            )}
            {document.status === "failed"
              ? `Failed: ${document.error_message ?? "Unknown error"}`
              : "Processing…"}
          </p>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <GroupSelect
            label={`Group for ${displayName}`}
            hideLabel
            value={document.group_id}
            onChange={(groupId) => void handleGroupChange(groupId)}
            disabled={isBusy || updateMetadata.isPending}
            className="w-36"
          />
          <span>{document.chunk_count} chunks</span>
          <span aria-hidden="true">&middot;</span>
          <span>{formatFileSize(document.file_size)}</span>
          <span aria-hidden="true">&middot;</span>
          <span>{formatDate(document.created_at)}</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        {/* <label>-wraps-<input> (not a <button> triggering a ref'd input) — clicking
            the label natively activates the file input exactly once. A button-wraps-input
            pattern was tried earlier in this project and caused a double-file-dialog
            re-entrancy bug; this mirrors the fix already used by UploadDropzone. */}
        <label
          className={
            isBusy || replaceDocument.isPending
              ? "cursor-not-allowed text-sm text-muted-foreground/50"
              : "cursor-pointer text-sm text-muted-foreground underline-offset-2 hover:underline has-[input:focus-visible]:ring-3 has-[input:focus-visible]:ring-ring/50"
          }
        >
          Replace
          <input
            type="file"
            className="sr-only"
            disabled={isBusy || replaceDocument.isPending}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleReplaceFileSelected(file)
              // Allow re-selecting the same file next time (change won't fire otherwise).
              e.target.value = ""
            }}
          />
        </label>

        <Dialog open={isConfirmOpen} onOpenChange={setIsConfirmOpen}>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete ${displayName}`}
            disabled={isBusy}
            onClick={() => setIsConfirmOpen(true)}
          >
            <Trash2 className="size-4 text-destructive" />
          </Button>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete this document?</DialogTitle>
              <DialogDescription>
                This removes &ldquo;{displayName}&rdquo; and all of its chunks too. This can&apos;t
                be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setIsConfirmOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={() => void handleConfirmDelete()}
                disabled={deleteDocument.isPending}
              >
                {deleteDocument.isPending ? "Deleting…" : "Delete"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </li>
  )
}
