import { useEffect, useRef } from "react"
import { MessageSquare } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { MessageBubble } from "@/components/chat/MessageBubble"
import { useDocuments } from "@/api/hooks/useDocuments"
import type { ChatMessage } from "@/api/hooks/useChat"

interface MessageListProps {
  messages: ChatMessage[]
  isStreaming: boolean
  isLoadingHistory: boolean
}

export function MessageList({ messages, isStreaming, isLoadingHistory }: MessageListProps) {
  const documentsQuery = useDocuments()
  const bottomRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to the newest content as tokens stream in. Deliberately simple
  // (always scrolls to bottom on any message change) per the task's "don't fight
  // the user if they scroll up" being a nice-to-have, not a requirement.
  useEffect(() => {
    // jsdom (test environment) doesn't implement scrollIntoView at all, unlike
    // real browsers where it's always present — guard so tests don't crash.
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" })
  }, [messages])

  if (isLoadingHistory) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-6">
        <Skeleton className="h-14 w-2/3 self-end rounded-2xl" />
        <Skeleton className="h-20 w-3/4 rounded-2xl" />
        <Skeleton className="h-10 w-1/2 self-end rounded-2xl" />
      </div>
    )
  }

  if (messages.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center text-muted-foreground">
        <MessageSquare className="size-8" aria-hidden="true" />
        <p className="text-sm">Ask something about your notes.</p>
      </div>
    )
  }

  const lastMessageId = messages[messages.length - 1]?.id

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-6">
      {messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          isStreamingThisMessage={
            isStreaming && message.role === "assistant" && message.id === lastMessageId
          }
          documents={documentsQuery.data}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
