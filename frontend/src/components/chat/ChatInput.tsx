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
  // The chat's current group (existing conversation's group_id, or a
  // not-yet-sent new chat's pending group) — an attach uploads straight
  // into this group with no prompt, including when it's null/ungrouped.
  // When null, a successful attach opens the group-assignment popover below
  // (T10b) instead of staying silently ungrouped.
  attachGroupId: string | null
}

// Backend's documented top_k range (see ChatRequest schema) — values outside
// this are rejected server-side, so out-of-range input is clamped client-side
// before it's ever sent rather than surfacing a 422 round-trip.
const TOP_K_MIN = 1
const TOP_K_MAX = 20

export function ChatInput({ isStreaming, onSend, onStop, attachGroupId }: ChatInputProps) {
  const [value, setValue] = useState("")
  const [isFiltersOpen, setIsFiltersOpen] = useState(false)
  const [tags, setTags] = useState("")
  const [topK, setTopK] = useState("")

  const textareaId = useId()
  const tagsId = useId()
  const topKId = useId()
  const fileInputId = useId()

  // Attach state: `uploadingFilename` covers the initial POST (no document
  // id exists yet), `attachedDocumentId` covers everything after — its live
  // status is read from useDocuments()'s already-polling list rather than a
  // second dedicated status hook (see design doc §4.2's "implementation
  // detail for the ticket" note).
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadingFilename, setUploadingFilename] = useState<string | null>(null)
  const [attachedDocumentId, setAttachedDocumentId] = useState<string | null>(null)
  // Open only when the chat is ungrouped and an attach just succeeded — reset
  // per-attach in handleFileSelected, not persisted, so it re-appears on every
  // subsequent attach while the chat stays ungrouped (T10b's explicit "no
  // don't-ask-again state" requirement).
  const [isGroupPromptOpen, setIsGroupPromptOpen] = useState(false)
  const uploadDocument = useUploadDocument()
  const updateDocumentGroup = useUpdateDocumentMetadata()
  const documentsQuery = useDocuments()
  const attachedDocument = documentsQuery.data?.find((doc) => doc.id === attachedDocumentId)

  function clearAttachment() {
    setUploadingFilename(null)
    setAttachedDocumentId(null)
    setIsGroupPromptOpen(false)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  function handleFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Cleared immediately (not after upload resolves) so picking the exact
    // same file again later still fires a change event.
    event.target.value = ""
    if (!file) return

    setAttachedDocumentId(null)
    setIsGroupPromptOpen(false)
    setUploadingFilename(file.name)
    uploadDocument.mutate(
      { file, groupId: attachGroupId ?? undefined },
      {
        onSuccess: (document) => {
          setUploadingFilename(null)
          setAttachedDocumentId(document.id)
          // Uploaded ungrouped (per #11's default) — offer to assign a group
          // now, rather than leaving it silently ungrouped. A chat that
          // already has a group skips this entirely (see #11's regression
          // check).
          if (attachGroupId === null) setIsGroupPromptOpen(true)
        },
        onError: (error) => {
          setUploadingFilename(null)
          toast.error(
            error instanceof UploadError
              ? messageForUploadError(error)
              : "Upload failed. Please try again."
          )
        },
      }
    )
  }

  async function handleGroupPromptChange(groupId: string | null) {
    if (!attachedDocumentId) return
    try {
      await updateDocumentGroup.mutateAsync({ documentId: attachedDocumentId, groupId })
    } catch {
      toast.error("Failed to update the document's group.")
    } finally {
      setIsGroupPromptOpen(false)
    }
  }

  const canSend = value.trim().length > 0 && !isStreaming

  function submit() {
    if (!canSend) return
    const parsedTags = tags
      .split(",")
      .map((tag) => tag.trim())
      .filter((tag) => tag.length > 0)
    const parsedTopK = topK.trim().length > 0 ? Number(topK) : undefined
    // Non-integer/NaN input is dropped (falls back to the backend's own default)
    // rather than sending a value the API would reject; in-range integers are
    // clamped defensively even though the `min`/`max` input attrs already
    // discourage out-of-range typing.
    const validTopK =
      parsedTopK !== undefined && Number.isInteger(parsedTopK)
        ? Math.min(TOP_K_MAX, Math.max(TOP_K_MIN, parsedTopK))
        : undefined

    onSend(value, {
      tags: parsedTags.length > 0 ? parsedTags : undefined,
      topK: validTopK,
    })
    setValue("")
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

        {(uploadingFilename || attachedDocument) && (
          <Popover open={isGroupPromptOpen} onOpenChange={setIsGroupPromptOpen}>
            {/* No `asChild` — AttachmentChip is a plain function component that
                doesn't forward a ref, so PopoverAnchor renders its own wrapping
                element here (needed for Radix's Popper positioning) rather than
                trying to attach a ref directly to the chip. */}
            <PopoverAnchor className="self-start">
              <AttachmentChip
                filename={uploadingFilename ?? attachedDocument?.filename ?? ""}
                status={uploadingFilename ? "uploading" : (attachedDocument?.status ?? "pending")}
                errorMessage={attachedDocument?.error_message}
                onDismiss={clearAttachment}
              />
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
            accept={ACCEPTED_FILE_TYPES}
            onChange={handleFileSelected}
            disabled={uploadDocument.isPending}
            className="sr-only"
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="Attach a file"
            disabled={uploadDocument.isPending}
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
