import { useId, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react"
import { Paperclip, Send, Square } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import { AttachmentChip } from "@/components/chat/AttachmentChip"
import { ACCEPTED_FILE_TYPES } from "@/components/documents/UploadDropzone"
import { GroupSelect } from "@/components/documents/GroupSelect"
import {
  useDocuments,
  useUpdateDocumentMetadata,
  useUploadDocument,
} from "@/api/hooks/useDocuments"
import { UploadError } from "@/api/uploadError"
import { messageForUploadError } from "@/lib/uploadErrorMessage"
import type { ChatSendFilters } from "@/api/hooks/useChat"

interface ChatInputProps {
  isStreaming: boolean
  onSend: (question: string, filters?: ChatSendFilters) => void
  onStop: () => void
  attachGroupId: string | null
}

const MAX_ATTACHMENTS = 5

// Statuses that block the Send button while any attachment is in one of them.
// "uploading" is a client-only state (POST in flight, no document id yet);
// "processing" and "failed" come from the backend's DocumentResponse.status.
const BLOCKING_STATUSES: ReadonlySet<string> = new Set(["uploading", "processing", "failed"])

// Per-file state tracked in the attachments array. A file starts as
// "uploading" (filename only, no document id yet), transitions to
// "attached" on upload success (has a document id, status polled from
// useDocuments), or "error" on upload failure.
interface AttachmentEntry {
  key: string
  filename: string
  documentId: string | null
  error: string | null
}

interface ResolvedAttachmentStatus {
  status: "uploading" | string
  errorMessage: string | null
}

export function ChatInput({ isStreaming, onSend, onStop, attachGroupId }: ChatInputProps) {
  const [value, setValue] = useState("")

  const textareaId = useId()
  const fileInputId = useId()

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [attachments, setAttachments] = useState<AttachmentEntry[]>([])
  const [isGroupPromptOpen, setIsGroupPromptOpen] = useState(false)
  const uploadDocument = useUploadDocument()
  const updateDocumentGroup = useUpdateDocumentMetadata()
  const documentsQuery = useDocuments()

  function removeAttachment(key: string) {
    setAttachments((prev) => prev.filter((a) => a.key !== key))
  }

  function clearAllAttachments() {
    setAttachments([])
    setIsGroupPromptOpen(false)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  // Called when the whole batch has resolved (every file either succeeded or
  // failed) — opens the group-assignment popover if any uploads succeeded
  // and the chat is ungrouped.
  function onBatchSettled(hasAnySuccess: boolean) {
    if (hasAnySuccess && attachGroupId === null) {
      setIsGroupPromptOpen(true)
    }
  }

  function handleFilesSelected(event: ChangeEvent<HTMLInputElement>) {
    // Snapshot into a plain array BEFORE clearing the input — FileList is a
    // live reference that empties when value is reset; without this, the
    // batch would always be [] and no chips would appear.
    const selected = Array.from(event.target.files ?? [])
    event.target.value = ""
    if (selected.length === 0) return

    const remaining = MAX_ATTACHMENTS - attachments.length
    const batch = selected.slice(0, remaining)
    if (selected.length > remaining) {
      toast.error(
        `Maximum ${MAX_ATTACHMENTS} files per batch. ${selected.length - remaining} file(s) skipped.`
      )
    }

    let pendingCount = batch.length
    let successCount = 0

    for (const file of batch) {
      const key = crypto.randomUUID()
      const entry: AttachmentEntry = {
        key,
        filename: file.name,
        documentId: null,
        error: null,
      }
      setAttachments((prev) => [...prev, entry])

      uploadDocument.mutate(
        { file, groupId: attachGroupId ?? undefined },
        {
          onSuccess: (document) => {
            setAttachments((prev) =>
              prev.map((a) => (a.key === key ? { ...a, documentId: document.id } : a))
            )
            successCount += 1
            pendingCount -= 1
            if (pendingCount === 0) onBatchSettled(successCount > 0)
          },
          onError: (error) => {
            const message =
              error instanceof UploadError
                ? messageForUploadError(error)
                : "Upload failed. Please try again."
            setAttachments((prev) =>
              prev.map((a) => (a.key === key ? { ...a, error: message } : a))
            )
            pendingCount -= 1
            if (pendingCount === 0) onBatchSettled(successCount > 0)
          },
        }
      )
    }
  }

  async function handleGroupPromptChange(groupId: string | null) {
    const docIds = attachments.filter((a) => a.documentId !== null).map((a) => a.documentId!)
    try {
      await Promise.all(
        docIds.map((documentId) => updateDocumentGroup.mutateAsync({ documentId, groupId }))
      )
    } catch {
      toast.error("Failed to update the documents' group.")
    } finally {
      setIsGroupPromptOpen(false)
    }
  }

  // Resolves an attachment entry's live status from internal error state or
  // the polled documents list (two distinct sources — the entry carries
  // client-side upload errors while useDocuments carries backend processing
  // status).
  function resolveAttachmentStatus(entry: AttachmentEntry): ResolvedAttachmentStatus {
    if (entry.error !== null) return { status: "failed", errorMessage: entry.error }
    if (entry.documentId === null) return { status: "uploading", errorMessage: null }
    const doc = documentsQuery.data?.find((d) => d.id === entry.documentId)
    // Falls back to "processing" (a blocking status) when the document hasn't
    // appeared in the polled list yet — the upload POST returned a document id
    // but useDocuments hasn't refetched. Defaulting to "ready" here would
    // prematurely unblock Send before the backend confirms the document is done.
    return {
      status: doc?.status ?? "processing",
      errorMessage: doc?.error_message ?? null,
    }
  }

  const hasBlockingAttachment = attachments.some((a) => {
    const { status } = resolveAttachmentStatus(a)
    return BLOCKING_STATUSES.has(status)
  })
  const canSend = value.trim().length > 0 && !isStreaming && !hasBlockingAttachment

  function submit() {
    if (!canSend) return

    const attachedDocumentIds = attachments
      .filter((a) => a.documentId !== null)
      .map((a) => a.documentId!)

    onSend(value, {
      attachedDocumentIds: attachedDocumentIds.length > 0 ? attachedDocumentIds : undefined,
    })
    setValue("")
    clearAllAttachments()
  }

  // Enter sends (matches every mainstream chat UI's convention); Shift+Enter
  // inserts a newline for multi-line questions. isComposing is checked so IME
  // composition (e.g. typing Japanese/Chinese) confirming via Enter doesn't
  // prematurely submit the message.
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  const hasAttachments = attachments.length > 0

  return (
    <div className="border-t border-border bg-background px-4 py-3">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-2">
        {hasAttachments && (
          <Popover open={isGroupPromptOpen} onOpenChange={setIsGroupPromptOpen}>
            {/* No `asChild` — AttachmentChip is a plain function component that
                doesn't forward a ref, so PopoverAnchor renders its own wrapping
                element (needed for Radix's Popper positioning) rather than trying
                to attach a ref directly to the chip. */}
            <PopoverAnchor className="self-start">
              <div className="flex flex-wrap gap-2" role="list" aria-label="Attached files">
                {attachments.map((entry) => {
                  const { status, errorMessage } = resolveAttachmentStatus(entry)
                  return (
                    <div key={entry.key} role="listitem">
                      <AttachmentChip
                        filename={entry.filename}
                        status={status}
                        errorMessage={errorMessage}
                        onDismiss={() => removeAttachment(entry.key)}
                      />
                    </div>
                  )
                })}
              </div>
            </PopoverAnchor>
            <PopoverContent>
              <GroupSelect
                label="Add to a group?"
                value={null}
                onChange={(groupId) => void handleGroupPromptChange(groupId)}
                disabled={updateDocumentGroup.isPending}
              />
            </PopoverContent>
          </Popover>
        )}

        <div className="flex items-end gap-2">
          <Label htmlFor={textareaId} className="sr-only">
            Message
          </Label>
          <Label htmlFor={fileInputId} className="sr-only">
            Attach a file
          </Label>
          <input
            ref={fileInputRef}
            id={fileInputId}
            type="file"
            multiple
            accept={ACCEPTED_FILE_TYPES}
            onChange={handleFilesSelected}
            className="sr-only"
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="Attach a file"
            disabled={attachments.length >= MAX_ATTACHMENTS}
            onClick={() => fileInputRef.current?.click()}
          >
            <Paperclip className="size-4" aria-hidden="true" />
          </Button>
          <Textarea
            id={textareaId}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask something about your notes…"
            className="max-h-40 flex-1 resize-none"
            rows={1}
          />
          {isStreaming ? (
            <Button type="button" variant="outline" size="icon" aria-label="Stop" onClick={onStop}>
              <Square className="size-4" aria-hidden="true" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              aria-label="Send"
              disabled={!canSend}
              onClick={submit}
            >
              <Send className="size-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
