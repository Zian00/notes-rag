import { useCallback, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { cn } from "@/lib/utils"
import { AttachmentCard } from "@/components/chat/AttachmentCard"
import { Citations } from "@/components/chat/Citations"
import { StreamingCursor } from "@/components/chat/StreamingCursor"
import { ThinkingIndicator } from "@/components/chat/ThinkingIndicator"
import { useMarkdownComponents } from "@/components/chat/useMarkdownComponents"
import { rehypeCitationMarkers } from "@/lib/rehypeCitationMarkers"
import { rehypeStreamingCursor } from "@/lib/rehypeStreamingCursor"
import type { ChatMessage } from "@/api/hooks/useChat"
import type { components } from "@/api/schema"

type DocumentResponse = components["schemas"]["DocumentResponse"]

interface MessageBubbleProps {
  message: ChatMessage
  // True only for the single in-progress assistant turn (last message while
  // useChat.isStreaming) — drives the typing cursor, not any other bubble.
  isStreamingThisMessage: boolean
  // The full documents list, passed from the parent so AttachmentCard doesn't
  // need its own useDocuments() call — resolves filename and deleted state here.
  documents?: DocumentResponse[]
}

export function MessageBubble({ message, isStreamingThisMessage, documents }: MessageBubbleProps) {
  const isUser = message.role === "user"
  // User messages and error messages are never markdown-parsed: user input
  // shouldn't be reinterpreted as formatting, and error strings are app-
  // generated diagnostics, not model answers meant to be formatted.
  const renderAsMarkdown = !isUser && !message.error
  const citationCount = message.citations?.length ?? 0

  const [citationsExpanded, setCitationsExpanded] = useState(false)
  const [highlightedCitationIndex, setHighlightedCitationIndex] = useState<number | null>(null)

  // A "[n]" marker click expands the Citations list (if collapsed) and scrolls
  // to/highlights the matching entry — see docs/design/2026-07-31-markdown-
  // rendering-citation-fidelity-design.md §6.7. rehypeCitationMarkers only ever
  // turns "[n]" into a clickable button for n within 1..citationCount, so this
  // index is always valid by the time it's clicked.
  const handleCitationClick = useCallback(
    (n: number) => {
      setCitationsExpanded(true)
      setHighlightedCitationIndex(n - 1)
      requestAnimationFrame(() => {
        document
          .getElementById(`${message.id}-citation-${n - 1}`)
          ?.scrollIntoView?.({ behavior: "smooth", block: "nearest" })
      })
    },
    [message.id]
  )

  const markdownComponents = useMarkdownComponents(handleCitationClick)

  const hasAttachments =
    isUser && message.attachedDocumentIds && message.attachedDocumentIds.length > 0

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "flex max-w-[85%] flex-col sm:max-w-[70%]",
          isUser && "items-end",
          !isUser && "items-start"
        )}
      >
        {hasAttachments && (
          <div className="mb-1.5 flex flex-wrap gap-1.5">
            {message.attachedDocumentIds!.map((docId) => {
              const doc = documents?.find((d) => d.id === docId)
              const isDeleted = documents !== undefined && doc === undefined
              return (
                <AttachmentCard
                  key={docId}
                  documentId={docId}
                  filename={doc?.filename ?? null}
                  isDeleted={isDeleted}
                />
              )
            })}
          </div>
        )}
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm",
            isUser && "bg-primary text-primary-foreground",
            !isUser && !message.error && "bg-muted text-foreground",
            !isUser && message.error && "bg-destructive/10 text-destructive"
          )}
        >
          {renderAsMarkdown ? (
            <div className="flex flex-col gap-2 wrap-anywhere">
              {/* Nothing to parse yet (waiting for the first token) — show the
                "Thinking…" indicator instead of an empty bubble; the cursor
                plugin below needs at least one text node to attach to anyway. */}
              {isStreamingThisMessage && !message.content ? (
                <ThinkingIndicator />
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  // Citation markers must be resolved before the cursor plugin
                  // runs, so the cursor search sees the tree post-splitting —
                  // otherwise it could search the pre-split text for "the last
                  // text node" and land somewhere a split later moves past.
                  rehypePlugins={[
                    [rehypeCitationMarkers, citationCount],
                    ...(isStreamingThisMessage ? [rehypeStreamingCursor] : []),
                  ]}
                  components={markdownComponents}
                >
                  {message.content}
                </ReactMarkdown>
              )}
            </div>
          ) : (
            <p className="whitespace-pre-wrap wrap-anywhere">
              {message.content}
              {isStreamingThisMessage && <StreamingCursor />}
            </p>
          )}

          {!isUser && message.citations && message.citations.length > 0 && (
            <Citations
              citations={message.citations}
              isExpanded={citationsExpanded}
              onExpandedChange={setCitationsExpanded}
              highlightedIndex={highlightedCitationIndex}
              idPrefix={message.id}
            />
          )}
        </div>
      </div>
    </div>
  )
}
