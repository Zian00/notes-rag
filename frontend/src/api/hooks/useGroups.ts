import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query"
import { $api, fetchClient } from "@/api/client"
import { getConversationsListKey } from "@/api/hooks/useConversations"
import type { components } from "@/api/schema"

type GroupResponse = components["schemas"]["GroupResponse"]
type GroupDeleteResponse = components["schemas"]["GroupDeleteResponse"]

// Derives the exact query key openapi-react-query uses for the groups list —
// same rationale as getConversationsListKey/getDocumentsListKey: invalidation
// can't silently drift if the library's key shape ever changes.
export function getGroupsListKey() {
  return $api.queryOptions("get", "/groups").queryKey
}

// Lists the current user's groups. Thin wrapper over openapi-react-query so
// callers don't need to know the call shape.
export function useGroups(): UseQueryResult<GroupResponse[], unknown> {
  return $api.useQuery("get", "/groups")
}

// Creates a group. The backend resolves a case-insensitive duplicate name to
// the *existing* group (200, not an error) rather than rejecting it — this
// is what makes "New group" safe to use as an inline-create affordance.
export function useCreateGroup(): UseMutationResult<GroupResponse, Error, string> {
  const queryClient = useQueryClient()
  const groupsListKey = getGroupsListKey()

  return useMutation<GroupResponse, Error, string>({
    mutationFn: async (name) => {
      const { data, error } = await fetchClient.POST("/groups", { body: { name } })
      if (error || !data) throw new Error("Failed to create group.")
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: groupsListKey })
    },
  })
}

export interface RenameGroupInput {
  groupId: string
  name: string
}

export function useRenameGroup(): UseMutationResult<GroupResponse, Error, RenameGroupInput> {
  const queryClient = useQueryClient()
  const groupsListKey = getGroupsListKey()

  return useMutation<GroupResponse, Error, RenameGroupInput>({
    mutationFn: async ({ groupId, name }) => {
      const { data, error, response } = await fetchClient.PATCH("/groups/{group_id}", {
        params: { path: { group_id: groupId } },
        body: { name },
      })
      if (error || !data) {
        if (response.status === 409) throw new Error("A group with that name already exists.")
        throw new Error("Failed to rename group.")
      }
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: groupsListKey })
    },
  })
}

// Deletes a group. The backend orphans (never deletes) the group's chats and
// documents to ungrouped first, then removes the group row, and reports how
// many of each were affected — invalidating conversations too since some of
// them just changed section.
export function useDeleteGroup(): UseMutationResult<GroupDeleteResponse, Error, string> {
  const queryClient = useQueryClient()
  const groupsListKey = getGroupsListKey()
  const conversationsListKey = getConversationsListKey()

  return useMutation<GroupDeleteResponse, Error, string>({
    mutationFn: async (groupId) => {
      const { data, error } = await fetchClient.DELETE("/groups/{group_id}", {
        params: { path: { group_id: groupId } },
      })
      if (error || !data) throw new Error("Failed to delete group.")
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: groupsListKey })
      void queryClient.invalidateQueries({ queryKey: conversationsListKey })
    },
  })
}
