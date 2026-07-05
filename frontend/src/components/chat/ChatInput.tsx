import { useId, useState, type KeyboardEvent } from "react"
import { ChevronDown, Send, Square } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { ChatSendFilters } from "@/api/hooks/useChat"

interface ChatInputProps {
  isStreaming: boolean
  onSend: (question: string, filters?: ChatSendFilters) => void
  onStop: () => void
}

// Backend's documented top_k range (see ChatRequest schema) — values outside
// this are rejected server-side, so out-of-range input is clamped client-side
// before it's ever sent rather than surfacing a 422 round-trip.
const TOP_K_MIN = 1
const TOP_K_MAX = 20

export function ChatInput({ isStreaming, onSend, onStop }: ChatInputProps) {
  const [value, setValue] = useState("")
  const [isFiltersOpen, setIsFiltersOpen] = useState(false)
  const [course, setCourse] = useState("")
  const [tags, setTags] = useState("")
  const [topK, setTopK] = useState("")

  const textareaId = useId()
  const courseId = useId()
  const tagsId = useId()
  const topKId = useId()

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
      course: course.trim() || undefined,
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
          <ChevronDown className={cn("size-3.5 transition-transform", isFiltersOpen && "rotate-180")} aria-hidden="true" />
          Filters
        </button>

        {isFiltersOpen && (
          <div className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-card p-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor={courseId}>Course</Label>
              <Input id={courseId} value={course} onChange={(event) => setCourse(event.target.value)} placeholder="e.g. cs101" />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor={tagsId}>Tags (comma-separated)</Label>
              <Input id={tagsId} value={tags} onChange={(event) => setTags(event.target.value)} placeholder="week1, midterm" />
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
            <Button type="button" size="icon" aria-label="Send" disabled={!canSend} onClick={submit}>
              <Send className="size-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
