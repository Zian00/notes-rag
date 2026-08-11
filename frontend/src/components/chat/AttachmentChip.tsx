import { Loader2, Paperclip, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface AttachmentChipProps {
  filename: string
  // "uploading" covers the initial POST (before the document has an id yet);
  // everything else is DocumentResponse.status verbatim — typed as a bare
  // `string` in the generated schema (not a literal union), so this matches
  // DocumentRow's own untyped string comparisons rather than fighting it.
  status: "uploading" | string
  errorMessage?: string | null
  onDismiss: () => void
}

// Shown above ChatInput's textarea while a chat-native attach (T10a) is in
// flight or still processing. Dismissing only clears this chip from the
// composer — the underlying Document is never deleted (see ADR-0001: an
// attached document is a normal group-scoped library item, not tied to
// this chat).
export function AttachmentChip({ filename, status, errorMessage, onDismiss }: AttachmentChipProps) {
  const isBusy = status === "uploading" || status === "pending" || status === "processing"

  return (
    <div className="flex items-center gap-2 self-start rounded-lg border border-border bg-card px-3 py-1.5 text-sm">
      <Paperclip className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="max-w-48 truncate font-medium">{filename}</span>
      <span
        className={cn(
          "flex items-center gap-1 text-xs",
          status === "failed" ? "text-destructive" : "text-muted-foreground"
        )}
      >
        {isBusy && <Loader2 className="size-3 animate-spin" aria-hidden="true" />}
        {status === "uploading" && "Uploading…"}
        {(status === "pending" || status === "processing") && "Processing…"}
        {status === "ready" && "Ready"}
        {status === "failed" && (errorMessage ?? "Failed")}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label="Remove attached file"
        onClick={onDismiss}
      >
        <X className="size-3.5" aria-hidden="true" />
      </Button>
    </div>
  )
}
