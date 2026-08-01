import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { streamChat, type Citation } from "@/api/chatStream"
import { getConversationsListKey } from "@/api/hooks/useConversations"

// UI-facing message shape for the live chat list. Distinct from the backend's
// MessageResponse (role/content only) because the UI also needs a stable React
// key and per-turn streaming/error state that the backend has no concept of.
export interface ChatMessage {
  id: string // stable key for React lists (generated per message, not derived from content)
  role: "user" | "assistant"
  content: string
  citations?: Citation[]
  error?: boolean // true if this assistant turn ended in an error frame / stream failure
}

export interface ChatSendFilters {
  course?: string
  tags?: string[]
  topK?: number
}

export interface UseChatOptions {
  conversationId?: string
  // Called exactly once per brand-new conversation, when the first `meta` frame
  // reports the id the backend just created — lets the page navigate to /chat/:id.
  onConversationCreated?: (id: string) => void
}

export interface UseChatResult {
  messages: ChatMessage[]
  isStreaming: boolean
  send: (question: string, filters?: ChatSendFilters) => Promise<void>
  stop: () => void
  reset: () => void
  // Seeds the live list from persisted history (Task 12's ChatPage, backed by
  // useConversation). Only replaces `messages` when it's currently empty — this
  // "seed when empty" rule is what lets the reset-guard above do the right thing
  // in every case: a genuinely different conversation clears messages first (so
  // seeding then applies), while a self-created new-chat's just-streamed turns
  // are never empty, so a late-arriving history fetch can't stomp on them.
  seed: (history: ChatMessage[]) => void
}

// Generates stable per-message ids without relying on Math.random or Date.now
// collisions. crypto.randomUUID is available in both jsdom (test) and real
// browser environments, so no polyfill/module-scope counter is needed.
function generateMessageId(): string {
  return crypto.randomUUID()
}

// Tunable "typewriter" reveal pace: deltas now arrive over the network as fast
// as the model + backend can send them (real per-token streaming), which is
// often faster than a comfortable reading pace. These control how fast
// buffered-but-not-yet-shown text visually appears, independent of actual
// network/token arrival timing — see enqueueAssistantContent below.
const REVEAL_INTERVAL_MS = 15
const REVEAL_CHARS_PER_TICK = 3

// Drives the live (in-progress) chat session: the growing message list plus the
// streaming state machine on top of Task 10's streamChat SSE generator.
//
// History seeding is intentionally NOT this hook's job — Task 12's ChatPage reads
// persisted history via useConversation(conversationId) and hands it to whatever
// renders the message list. This hook only ever manages the *live* turn(s) added
// during the current mount; on a route change to a different conversation, it
// resets to an empty live list rather than trying to merge with fetched history.
export function useChat(options?: UseChatOptions): UseChatResult {
  const { conversationId, onConversationCreated } = options ?? {}

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)

  // Mutable, not state: read/written mid-stream without needing a re-render,
  // and must survive across renders without being reset by them.
  const activeConversationIdRef = useRef<string | undefined>(conversationId)
  const abortControllerRef = useRef<AbortController | null>(null)

  const queryClient = useQueryClient()
  // Static key (doesn't depend on any props/state) — memoized with an empty dep
  // array so `send`'s identity below stays stable across renders instead of
  // recomputing a new array (and thus a new `send`) every time.
  const conversationsListKey = useMemo(() => getConversationsListKey(), [])

  // Opening a different conversation (route change) starts a fresh live session —
  // any messages from the previously-open chat don't belong in this one, and the
  // new chat's history (if any) is seeded by the caller via useConversation, not here.
  //
  // This project's lint config forbids both ref access during render (react-hooks/refs)
  // and the react.dev-documented "reset via ref comparison during render" pattern, so an
  // effect is the only lint-clean option left; the one-frame-of-stale-messages tradeoff
  // that pattern exists to avoid is acceptable here since Task 12 shows a route-level
  // loading/empty state while useConversation's history fetch is in flight anyway.
  useEffect(() => {
    // Only reset when the prop points at a DIFFERENT conversation (e.g. sidebar
    // navigation to another chat). When the prop merely catches up to an id this
    // hook just self-assigned from a `meta` frame (new-chat navigation), the live
    // messages are still valid — don't wipe them.
    if (conversationId !== activeConversationIdRef.current) {
      setMessages([])
    }
    activeConversationIdRef.current = conversationId
  }, [conversationId])

  // Unmount (or navigating away mid-stream) must tear down the in-flight fetch —
  // otherwise the browser keeps receiving SSE bytes for a component that no
  // longer exists. Task 10's parser reader.cancel()s in its `finally` once this
  // abort propagates.
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
    }
  }, [])

  // Note: `isStreaming` does NOT flip to false synchronously here — it settles
  // asynchronously once the in-flight `send()`'s `finally` block runs after the
  // abort propagates through the stream reader. Callers gating UI on `isStreaming`
  // should expect a brief delay between calling stop()/reset() and it updating.
  const stop = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  const reset = useCallback(() => {
    abortControllerRef.current?.abort()
    setMessages([])
    activeConversationIdRef.current = undefined
  }, [])

  const seed = useCallback((history: ChatMessage[]) => {
    // Functional update (not a `messages.length === 0` check in the outer scope)
    // so this stays correct even if called from an effect that doesn't have the
    // latest `messages` in its closure — the check and the write happen atomically.
    setMessages((prev) => (prev.length === 0 ? history : prev))
  }, [])

  const send = useCallback(
    async (question: string, filters?: ChatSendFilters) => {
      const trimmed = question.trim()
      if (isStreaming || trimmed.length === 0) return

      const isNewChat = activeConversationIdRef.current === undefined

      const userMessage: ChatMessage = { id: generateMessageId(), role: "user", content: trimmed }
      const assistantMessageId = generateMessageId()
      const assistantPlaceholder: ChatMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
      }

      setMessages((prev) => [...prev, userMessage, assistantPlaceholder])
      setIsStreaming(true)

      const controller = new AbortController()
      abortControllerRef.current = controller

      // Immutable per-field updaters for the one assistant message being streamed
      // into — avoids clobbering concurrent state updates (React 19 batches these,
      // but the map-by-id pattern is correct regardless of batching).
      const patchAssistantMessage = (patch: Partial<ChatMessage>) => {
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantMessageId ? { ...message, ...patch } : message
          )
        )
      }
      // `pending` buffers text that's arrived over the network but hasn't been
      // visually revealed yet; a fixed-rate interval drains it a few characters
      // at a time. `onDrained` lets the `finally` block below await full drain
      // (so isStreaming — and the streaming cursor — stays true until the
      // typewriter visually catches up, not just until the network finishes).
      let pending = ""
      let revealTimer: ReturnType<typeof setInterval> | null = null
      let onDrained: (() => void) | null = null
      const revealTick = () => {
        if (pending.length > 0) {
          const chunk = pending.slice(0, REVEAL_CHARS_PER_TICK)
          pending = pending.slice(REVEAL_CHARS_PER_TICK)
          setMessages((prev) =>
            prev.map((message) =>
              message.id === assistantMessageId
                ? { ...message, content: message.content + chunk }
                : message
            )
          )
        }
        if (pending.length === 0) {
          if (revealTimer !== null) {
            clearInterval(revealTimer)
            revealTimer = null
          }
          onDrained?.()
          onDrained = null
        }
      }
      const enqueueAssistantContent = (delta: string) => {
        pending += delta
        if (revealTimer === null) {
          revealTimer = setInterval(revealTick, REVEAL_INTERVAL_MS)
        }
      }
      const waitForRevealToDrain = () =>
        new Promise<void>((resolve) => {
          if (pending.length === 0 && revealTimer === null) {
            resolve()
            return
          }
          onDrained = resolve
        })

      try {
        const frames = streamChat(
          {
            question: trimmed,
            conversation_id: activeConversationIdRef.current,
            course: filters?.course,
            tags: filters?.tags,
            top_k: filters?.topK,
          },
          controller.signal
        )

        for await (const frame of frames) {
          if (frame.event === "meta") {
            activeConversationIdRef.current = frame.data.conversation_id
            // Only fire for a genuinely new chat (no id when send() was called) —
            // a follow-up question in an already-open conversation reports the
            // same id again and must NOT re-trigger a navigation.
            if (isNewChat) {
              onConversationCreated?.(frame.data.conversation_id)
            }
          } else if (frame.event === "token") {
            enqueueAssistantContent(frame.data.delta)
          } else if (frame.event === "citations") {
            patchAssistantMessage({ citations: frame.data })
          } else if (frame.event === "error") {
            // Discard any not-yet-revealed partial answer text — content is
            // about to be replaced wholesale with the error message, so there's
            // nothing left worth typewriter-draining.
            pending = ""
            patchAssistantMessage({ error: true, content: frame.data.detail })
            break
          } else if (frame.event === "done") {
            break
          }
        }
      } catch (err) {
        // An aborted stream (stop() or unmount) is a user/lifecycle action, not a
        // failure — the assistant message should stay as-is (whatever content
        // streamed in before cancellation) rather than being marked errored.
        const aborted =
          controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")
        if (!aborted) {
          pending = "" // about to replace content wholesale — see the SSE error branch above
          patchAssistantMessage({ error: true, content: "Something went wrong. Please try again." })
        }
      } finally {
        // Let the typewriter finish revealing whatever's still buffered before
        // clearing isStreaming — the assistant should still look like it's
        // "responding" until the visible text has actually caught up, even
        // though the network stream itself already finished.
        await waitForRevealToDrain()
        setIsStreaming(false)
        abortControllerRef.current = null
        // A new chat now exists, or an existing one just moved to the top of the
        // list (updated_at bumped) — refresh the sidebar's list query either way.
        void queryClient.invalidateQueries({ queryKey: conversationsListKey })
      }
    },
    [isStreaming, onConversationCreated, queryClient, conversationsListKey]
  )

  return { messages, isStreaming, send, stop, reset, seed }
}
