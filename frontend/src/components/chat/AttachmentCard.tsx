import { Paperclip } from "lucide-react"
import { cn } from "@/lib/utils"

interface AttachmentCardProps {
  documentId: string
  // Filename resolved by the parent from its documents query — passed as a
  // prop so this component doesn't need its own useDocuments() call (was
  // flagged as Feature Envy in review). Null when the document has been
  // deleted and the filename is unrecoverable from the current library.
  filename: string | null
  isDeleted: boolean
}

// Rendered above a user message bubble in the chat history for each
// document the user attached on that turn. Links to the download
// endpoint in a new tab; shows "(deleted)" grayed out when the
// document no longer exists in the library.
export function AttachmentCard({ documentId, filename, isDeleted }: AttachmentCardProps) {
  const displayName = filename ?? "Attached file"
  const href = `/api/documents/${documentId}/download`

  if (isDeleted) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-muted/50 px-3 py-1 text-xs text-muted-foreground line-through">
        <Paperclip className="size-3 shrink-0" aria-hidden="true" />
        {displayName}
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
      {displayName}
    </a>
  )
}
