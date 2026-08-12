import { useId, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react"
import { ChevronDown, Paperclip, Send, Square } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
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

const TOP_K_MIN = 1
const TOP_K_MAX = 20
const MAX_ATTACHMENTS = 5

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

export function ChatInput({ isStreaming, onSend, onStop, attachGroupId }: ChatInputProps) {
  const [value, setValue] = useState("")
  const [isFiltersOpen, setIsFiltersOpen] = useState(false)
  const [tags, setTags] = useState("")
  const [topK, setTopK] = useState("")

  const textareaId = useId()
  const tagsId = useId()
  const topKId = useId()
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

  function handleFilesSelected(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files
    event.target.value = ""
    if (!files || files.length === 0) return

    const remaining = MAX_ATTACHMENTS - attachments.length
    const batch = Array.from(files).slice(0, remaining)
    if (files.length > remaining) {
      toast.error(`Maximum ${MAX_ATTACHMENTS} files per message. ${files.length - remaining} file(s) skipped.`)
    }

    let pendingSuccessCount = batch.length

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
            pendingSuccessCount -= 1
            // Open the group popover once for the whole batch, after ALL
            // uploads in the batch have resolved (not per-file).
            if (pendingSuccessCount === 0 && attachGroupId === null) {
              setIsGroupPromptOpen(true)
            }
          },
          onError: (error) => {
            const message =
              error instanceof UploadError
                ? messageForUploadError(error)
                : "Upload failed. Please try again."
            setAttachments((prev) =>
              prev.map((a) => (a.key === key ? { ...a, error: message } : a))
            )
            pendingSuccessCount -= 1
          },
        }
      )
    }
  }

  async function handleGroupPromptChange(groupId: string | null) {
    const docIds = attachments
      .filter((a) => a.documentId !== null)
      .map((a) => a.documentId!)
    try {
      await Promise.all(
        docIds.map((documentId) =>
          updateDocumentGroup.mutateAsync({ documentId, groupId })
        )
      )
    } catch {
      toast.error("Failed to update the documents' group.")
    } finally {
      setIsGroupPromptOpen(false)
    }
  }

  // Resolve each attachment's live status from the polled documents list.
  function getAttachmentStatus(entry: AttachmentEntry): {
    status: "uploading" | string
    errorMessage: string | null
  } {
    if (entry.error !== null) return { status: "failed", errorMessage: entry.error }
    if (entry.documentId === null) return { status: "uploading", errorMessage: null }
    const doc = documentsQuery.data?.find((d) => d.id === entry.documentId)
    return {
      status: doc?.status ?? "pending",
      errorMessage: doc?.error_message ?? null,
    }
  }

  // Send is blocked while any attachment is uploading, processing, or failed.
  const hasBlockingAttachment = attachments.some((a) => {
    const { status } = getAttachmentStatus(a)
    return status === "uploading" || status === "pending" || status === "processing" || status === "failed"
  })
  const canSend = value.trim().length > 0 && !isStreaming && !hasBlockingAttachment

  function submit() {
    if (!canSend) return
    const parsedTags = tags
      .split(",")
      .map((tag) => tag.trim())
      .filter((tag) => tag.length > 0)
    const parsedTopK = topK.trim().length > 0 ? Number(topK) : undefined
    const validTopK =
      parsedTopK !== undefined && Number.isInteger(parsedTopK)
        ? Math.min(TOP_K_MAX, Math.max(TOP_K_MIN, parsedTopK))
        : undefined

    const attachedDocumentIds = attachments
      .filter((a) => a.documentId !== null)
      .map((a) => a.documentId!)

    onSend(value, {
      tags: parsedTags.length > 0 ? parsedTags : undefined,
      topK: validTopK,
      attachedDocumentIds: attachedDocumentIds.length > 0 ? attachedDocumentIds : undefined,
    })
    setValue("")
    clearAllAttachments()
  }

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
        <button
          type="button"
          aria-expanded={isFiltersOpen}
          onClick={() => setIsFiltersOpen((open) => !open)}
          className="flex w-fit items-center gap-1 self-start rounded text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <ChevronDown
            className={cn("size-3.5 transition-transform", isFiltersOpen && "rotate-180")}
            aria-hidden="true"
          />
          Filters
        </button>

        {hasAttachments && (
          <Popover open={isGroupPromptOpen} onOpenChange={setIsGroupPromptOpen}>
            <PopoverAnchor className="self-start">
              <div className="flex flex-wrap gap-2" role="list" aria-label="Attached files">
                {attachments.map((entry) => {
                  const { status, errorMessage } = getAttachmentStatus(entry)
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

        {isFiltersOpen && (
          <div className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-card p-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor={tagsId}>Tags (comma-separated)</Label>
              <Input
                id={tagsId}
                value={tags}
                onChange={(event) => setTags(event.target.value)}
                placeholder="week1, midterm"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor={topKId}>Top K</Label>
              <Input
                id={topKId}
                type="number"
                min={TOP_K_MIN}
                max={TOP_K_MAX}
                value={topK}
                onChange={(event) => setTopK(event.target.value)}
                placeholder="5"
              />
            </div>
          </div>
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
