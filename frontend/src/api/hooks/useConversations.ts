import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query"
import { $api, fetchClient } from "@/api/client"
import { DeleteError } from "@/api/deleteError"
import type { components } from "@/api/schema"

type ConversationResponse = components["schemas"]["ConversationResponse"]
type ConversationDetail = components["schemas"]["ConversationDetail"]

// Derives the exact query key openapi-react-query uses for the conversations list —
// mirrors getDocumentsListKey in useDocuments.ts so invalidation can't silently drift
// if the library's key shape changes (see that file's comment for the full rationale).
// Exported so other hooks that need this exact key (e.g. useChat's post-send
// invalidation) import it instead of re-deriving it — a second inline copy would
// silently drift if the library's key shape ever changes.
export function getConversationsListKey() {
  return $api.queryOptions("get", "/conversations").queryKey
}

// Same rationale as getConversationsListKey, for a single conversation's detail
// (history) query. Needed by useChat: finishing a turn changes that conversation's
// server-side history, so its cached detail must be invalidated or a later
// navigation back re-seeds the chat from pre-question history.
export function getConversationDetailKey(id: string) {
  return $api.queryOptions("get", "/conversations/{conversation_id}", {
    params: { path: { conversation_id: id } },
  }).queryKey
}

// Lists the current user's conversations, newest-first (backend's ordering).
// Thin wrapper over openapi-react-query so callers don't need to know the call shape.
export function useConversations(): UseQueryResult<ConversationResponse[], unknown> {
  return $api.useQuery("get", "/conversations")
}

// Fetches one conversation's full history (messages included). Disabled when `id`
// is undefined — a brand-new chat has no persisted history to seed from, and
// openapi-fetch's path param can't be omitted, so there's nothing valid to request.
export function useConversation(
  id: string | undefined
): UseQueryResult<ConversationDetail, unknown> {
  return $api.useQuery(
    "get",
    "/conversations/{conversation_id}",
    { params: { path: { conversation_id: id as string } } },
    { enabled: Boolean(id) }
  )
}

export interface UpdateConversationInput {
  id: string
  title?: string
  // `null` explicitly ungroups; `undefined` (the default when the key is
  // omitted below) leaves the group untouched — matches the backend's
  // model_fields_set PATCH semantics (see ConversationUpdate).
  groupId?: string | null
}

// Renames and/or moves a conversation via PATCH. Only the fields actually
// passed end up in the request body, so an omitted `groupId` never
// accidentally ungroups a chat that was only being renamed (and vice versa).
export function useUpdateConversation(): UseMutationResult<
  ConversationResponse,
  Error,
  UpdateConversationInput
> {
  const queryClient = useQueryClient()
  const conversationsListKey = getConversationsListKey()

  return useMutation<ConversationResponse, Error, UpdateConversationInput>({
    mutationFn: async ({ id, title, groupId }) => {
      const body: { title?: string; group_id?: string | null } = {}
      if (title !== undefined) body.title = title
      if (groupId !== undefined) body.group_id = groupId

      const { data, error, response } = await fetchClient.PATCH(
        "/conversations/{conversation_id}",
        {
          params: { path: { conversation_id: id } },
          body,
        }
      )
      if (error || !data) {
        if (response.status === 404) throw new Error("Conversation or group not found.")
        throw new Error("Failed to update conversation.")
      }
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: conversationsListKey })
    },
  })
}

// Deletes a conversation by id and invalidates the conversations list on success.
export function useDeleteConversation(): UseMutationResult<void, DeleteError, string> {
  const queryClient = useQueryClient()
  const conversationsListKey = getConversationsListKey()

  return useMutation<void, DeleteError, string>({
    mutationFn: async (conversationId) => {
      const { error, response } = await fetchClient.DELETE("/conversations/{conversation_id}", {
        params: { path: { conversation_id: conversationId } },
      })
      if (error) {
        throw new DeleteError(response.status, "Failed to delete conversation")
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: conversationsListKey })
    },
  })
}
