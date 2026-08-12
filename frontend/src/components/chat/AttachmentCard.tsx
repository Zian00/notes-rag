import { Paperclip } from "lucide-react"
import { cn } from "@/lib/utils"
import { useDocuments } from "@/api/hooks/useDocuments"

interface AttachmentCardProps {
  documentId: string
}

// Rendered above a user message bubble in the chat history for each
// document the user attached on that turn. Links to the download
// endpoint in a new tab; shows "(deleted)" grayed out when the
// document no longer exists in the library.
export function AttachmentCard({ documentId }: AttachmentCardProps) {
  const documentsQuery = useDocuments()
  const doc = documentsQuery.data?.find((d) => d.id === documentId)

  const isDeleted = documentsQuery.data !== undefined && doc === undefined
  const filename = doc?.filename ?? documentId
  const href = `/api/documents/${documentId}/download`

  if (isDeleted) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-muted/50 px-3 py-1 text-xs text-muted-foreground line-through">
        <Paperclip className="size-3 shrink-0" aria-hidden="true" />
        {filename}
        <span className="no-underline">(deleted)</span>
      </span>
    )
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1 text-xs font-medium",
        "transition-colors hover:bg-accent/40"
      )}
    >
      <Paperclip className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
      {filename}
    </a>
  )
}
