import { useEffect, useRef } from "react"
import { useLocation, useNavigate, useParams } from "react-router-dom"
import { MessageList } from "@/components/chat/MessageList"
import { ChatInput } from "@/components/chat/ChatInput"
import { useChat, type ChatMessage, type ChatSendFilters } from "@/api/hooks/useChat"
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
  const location = useLocation()

  const conversationQuery = useConversation(conversationId)

  const { messages, isStreaming, send, stop, seed, reset } = useChat({
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

  // Sidebar's "New chat" stamps a fresh nonce into location.state on every
  // click (see Sidebar.tsx) specifically so this fires even when navigating
  // to bare /chat is a route no-op (already there) — useChat's own route-param
  // reset-guard effect only reacts to an actual conversationId change, so it
  // can't clear a lingering live thread in that case on its own.
  const locationState = location.state as { newChatNonce?: string; groupId?: string } | null
  const newChatNonce = locationState?.newChatNonce
  // Sidebar's per-section "New chat" stamps the section's group id alongside the
  // nonce (see Sidebar.tsx) so the very first send() of this fresh thread can
  // pre-assign it — kept in a ref (not state) since it's read once by send()
  // and must not trigger a re-render on its own.
  const pendingGroupIdRef = useRef<string | undefined>(locationState?.groupId)
  const seenNewChatNonceRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    if (newChatNonce === undefined) return
    if (seenNewChatNonceRef.current === newChatNonce) return
    seenNewChatNonceRef.current = newChatNonce
    pendingGroupIdRef.current = locationState?.groupId
    reset()
    // A stale seed-guard would otherwise block re-seeding if the user opens
    // the same conversation again later in this mount's lifetime.
    seededConversationIdRef.current = undefined
  }, [newChatNonce, locationState?.groupId, reset])

  // Wraps ChatInput's onSend so the pending group id only ever rides along on
  // the first send() of a fresh thread — it's cleared immediately after use so
  // a later follow-up in the same conversation can't re-send (and potentially
  // re-widen) a group scope the backend would ignore anyway past creation.
  function handleSend(question: string, filters?: ChatSendFilters) {
    const groupId = pendingGroupIdRef.current
    pendingGroupIdRef.current = undefined
    void send(question, groupId ? { ...filters, groupId } : filters)
  }

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
          // Null for conversations answered before citations began being persisted
          // onto the answer message — those sources were never stored.
          citations: message.citations ?? undefined,
        }))
    )
    seededConversationIdRef.current = conversationId
  }, [conversationId, conversationQuery.data, seed])

  // Guarded on `messages.length === 0` too: a brand-new chat's onConversationCreated
  // navigation sets `conversationId` (kicking off a history fetch for the id it just
  // created) while the just-streamed live messages are already sitting in `messages` —
  // without this guard the loading skeleton would briefly replace real, already-visible
  // content purely because a redundant history fetch is in flight.
  const isLoadingHistory =
    Boolean(conversationId) && conversationQuery.isLoading && messages.length === 0

  // The group an attach (T10a) uploads into: an existing conversation's own
  // group_id, or — for a chat that hasn't been sent yet — whatever group
  // this fresh thread is pending into. Reads `locationState.groupId` rather
  // than `pendingGroupIdRef.current` (this project's lint forbids reading a
  // ref during render) — the two always agree before the first send, since
  // the ref is only ever seeded from this same value (see the effect above);
  // handleSend's post-send ref-clearing doesn't matter here because by then
  // `conversationId` is set and the first branch takes over.
  const attachGroupId: string | null =
    conversationId !== undefined
      ? conversationQuery.data?.group_id ?? null
      : locationState?.groupId ?? null

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <MessageList
          messages={messages}
          isStreaming={isStreaming}
          isLoadingHistory={isLoadingHistory}
        />
      </div>
      <ChatInput
        isStreaming={isStreaming}
        onSend={handleSend}
        onStop={stop}
        attachGroupId={attachGroupId}
      />
    </div>
  )
}
