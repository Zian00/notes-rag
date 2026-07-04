import { useEffect, useRef } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { MessageList } from "@/components/chat/MessageList"
import { ChatInput } from "@/components/chat/ChatInput"
import { useChat, type ChatMessage } from "@/api/hooks/useChat"
import { useConversation } from "@/api/hooks/useConversations"

// Backend message roles are typed as a bare `string` (schema.ts's MessageResponse),
// so this narrows defensively rather than trusting the API contract blindly —
// any unexpected role is dropped rather than mis-rendered as user or assistant.
function isRenderableRole(role: string): role is ChatMessage["role"] {
  return role === "user" || role === "assistant"
}

export function ChatPage() {
  const { conversationId } = useParams<{ conversationId?: string }>()
  const navigate = useNavigate()

  const conversationQuery = useConversation(conversationId)

  const { messages, isStreaming, send, stop, seed } = useChat({
    conversationId,
    // A brand-new chat's first `meta` frame reports the id the backend just
    // created — reflect it in the URL so reload/sharing/back-button all work,
    // using `replace` so the pre-navigation /chat entry isn't left in history.
    onConversationCreated: (id) => navigate(`/chat/${id}`, { replace: true }),
  })

  // Tracks which conversation id this effect has already attempted to seed, so
  // a history refetch (e.g. sidebar invalidation after send()) doesn't re-run
  // the seed body — seed() is a no-op once messages is non-empty anyway, but
  // this also lets a *new* conversationId (route change) be recognized as
  // needing a fresh seed attempt even though conversationQuery.data may not
  // have arrived yet on the render where the id first changes.
  const seededConversationIdRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    if (!conversationId) return
    if (seededConversationIdRef.current === conversationId) return
    const history = conversationQuery.data
    if (!history) return

    // Seed-when-empty rule (see useChat's `seed` doc comment): this is what
    // keeps a self-created new-chat's freshly-streamed turns from being wiped
    // by a late-arriving history fetch for the same conversation, while still
    // correctly seeding on "open existing conversation" and "reload /chat/:id".
    seed(
      history.messages
        .filter((message) => isRenderableRole(message.role))
        .map((message) => ({
          id: crypto.randomUUID(),
          role: message.role as ChatMessage["role"],
          content: message.content,
        })),
    )
    seededConversationIdRef.current = conversationId
  }, [conversationId, conversationQuery.data, seed])

  // Guarded on `messages.length === 0` too: a brand-new chat's onConversationCreated
  // navigation sets `conversationId` (kicking off a history fetch for the id it just
  // created) while the just-streamed live messages are already sitting in `messages` —
  // without this guard the loading skeleton would briefly replace real, already-visible
  // content purely because a redundant history fetch is in flight.
  const isLoadingHistory = Boolean(conversationId) && conversationQuery.isLoading && messages.length === 0

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <MessageList messages={messages} isStreaming={isStreaming} isLoadingHistory={isLoadingHistory} />
      </div>
      <ChatInput isStreaming={isStreaming} onSend={send} onStop={stop} />
    </div>
  )
}
