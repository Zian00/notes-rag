import { Paperclip } from "lucide-react"
import { cn } from "@/lib/utils"
import { API_BASE, getAccessToken } from "@/api/client"

interface AttachmentCardProps {
  documentId: string
  filename: string | null
  isDeleted: boolean
}

// Opens the file in a new tab via an authenticated fetch — plain <a href>
// navigations don't carry the Authorization header, so the download endpoint
// would reject them as unauthenticated.
async function openDownload(documentId: string) {
  const res = await fetch(`${API_BASE}/documents/${documentId}/download`, {
    headers: { Authorization: `Bearer ${getAccessToken()}` },
    credentials: "include",
  })
  if (!res.ok) return
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  window.open(url, "_blank")
  // Revoke after a short delay so the tab has time to load it.
  setTimeout(() => URL.revokeObjectURL(url), 30_000)
}

export function AttachmentCard({ documentId, filename, isDeleted }: AttachmentCardProps) {
  const displayName = filename ?? "Attached file"

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
    <button
      type="button"
      onClick={() => void openDownload(documentId)}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1 text-xs font-medium",
        "transition-colors hover:bg-accent/40"
      )}
    >
      <Paperclip className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
      {displayName}
    </button>
  )
}
