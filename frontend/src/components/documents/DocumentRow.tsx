import { useState } from "react"
import { Trash2 } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useDeleteDocument, useReplaceDocument } from "@/api/hooks/useDocuments"
import { DeleteError } from "@/api/deleteError"
import { formatDate, formatFileSize } from "@/lib/format"
import type { components } from "@/api/schema"

type DocumentResponse = components["schemas"]["DocumentResponse"]

interface DocumentRowProps {
  document: DocumentResponse
}

export function DocumentRow({ document }: DocumentRowProps) {
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const deleteDocument = useDeleteDocument()
  const replaceDocument = useReplaceDocument()

  const displayName = document.title ?? document.filename

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
                ? "mt-0.5 text-sm text-destructive"
                : "mt-0.5 text-sm text-muted-foreground"
            }
          >
            {document.status === "failed"
              ? `Failed: ${document.error_message ?? "Unknown error"}`
              : "Processing…"}
          </p>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {document.course && <Badge variant="secondary">{document.course}</Badge>}
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
        <label className="cursor-pointer text-sm text-muted-foreground underline-offset-2 hover:underline has-[input:focus-visible]:ring-3 has-[input:focus-visible]:ring-ring/50">
          Replace
          <input
            type="file"
            className="sr-only"
            disabled={replaceDocument.isPending}
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
