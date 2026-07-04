import { cn } from "@/lib/utils"
import { Citations } from "@/components/chat/Citations"
import type { ChatMessage } from "@/api/hooks/useChat"

interface MessageBubbleProps {
  message: ChatMessage
  // True only for the single in-progress assistant turn (last message while
  // useChat.isStreaming) — drives the typing cursor, not any other bubble.
  isStreamingThisMessage: boolean
}

export function MessageBubble({ message, isStreamingThisMessage }: MessageBubbleProps) {
  const isUser = message.role === "user"

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm sm:max-w-[70%]",
          isUser && "bg-primary text-primary-foreground",
          !isUser && !message.error && "bg-muted text-foreground",
          !isUser && message.error && "bg-destructive/10 text-destructive",
        )}
      >
        {/* whitespace-pre-wrap (not a markdown renderer) preserves the model's line
            breaks while staying within the "plain text is fine" requirement. */}
        <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">
          {message.content}
          {isStreamingThisMessage && (
            <span
              aria-hidden="true"
              className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse bg-current align-middle"
            />
          )}
        </p>

        {!isUser && message.citations && message.citations.length > 0 && (
          <Citations citations={message.citations} />
        )}
      </div>
    </div>
  )
}
