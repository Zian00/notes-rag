import { useEffect, useRef, useState } from "react"
import { useDroppable } from "@dnd-kit/core"
import { MoreHorizontal } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useDeleteGroup, useRenameGroup } from "@/api/hooks/useGroups"
import { $api } from "@/api/client"
import { useInlineEdit } from "@/hooks/useInlineEdit"
import { SectionHeader } from "@/components/layout/sidebar/SectionHeader"
import { ConversationItem } from "@/components/layout/sidebar/ConversationItem"
import type { components } from "@/api/schema"

type ConversationResponse = components["schemas"]["ConversationResponse"]
type GroupResponse = components["schemas"]["GroupResponse"]

interface GroupSectionProps {
  group: GroupResponse
  conversations: ConversationResponse[]
  groups: GroupResponse[]
  activeConversationId: string | undefined
  isCollapsed: boolean
  onToggle: () => void
  onNewChat: () => void
  onNavigate?: () => void
}

// Owns its own rename/delete UI state independently of sibling sections —
// opening one group's rename input or delete-confirm dialog must not affect
// any other group's.
export function GroupSection({
  group,
  conversations,
  groups,
  activeConversationId,
  isCollapsed,
  onToggle,
  onNewChat,
  onNavigate,
}: GroupSectionProps) {
  const rename = useInlineEdit()
  const renameInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (rename.isEditing) renameInputRef.current?.focus()
  }, [rename.isEditing])
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const renameGroup = useRenameGroup()
  const deleteGroup = useDeleteGroup()

  // Fetched only while the delete-confirm dialog is open — this is a
  // best-effort preview count for the confirmation copy, not a proactive
  // background subscription; the backend's DELETE response (used for the
  // success toast) is the source of truth.
  const documentsPreviewQuery = $api.useQuery(
    "get",
    "/documents",
    { params: { query: { group_id: group.id } } },
    { enabled: isConfirmOpen }
  )

  async function handleRenameSubmit() {
    const name = rename.value.trim()
    if (!name || name === group.name) {
      rename.close()
      return
    }
    try {
      await renameGroup.mutateAsync({ groupId: group.id, name })
      rename.close()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to rename group.")
    }
  }

  async function handleConfirmDelete() {
    try {
      const result = await deleteGroup.mutateAsync(group.id)
      setIsConfirmOpen(false)
      toast.success(
        `Deleted "${group.name}". ${result.chats_ungrouped} chat(s) and ${result.documents_ungrouped} document(s) moved to Ungrouped.`
      )
    } catch {
      toast.error("Failed to delete group.")
    }
  }

  const chatCountPreview = conversations.length
  const documentCountPreview = documentsPreviewQuery.data?.length

  // Drop target for moving a chat into this group by drag (see Sidebar's
  // DndContext). Spans the whole section — header + chat list — not just
  // the header, so dropping anywhere in the section works.
  const { setNodeRef, isOver } = useDroppable({ id: group.id })

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "mt-2 rounded-lg transition-colors",
        isOver && "bg-sidebar-accent/60 ring-1 ring-sidebar-ring/50"
      )}
    >
      {rename.isEditing ? (
        <form
          className="px-1"
          onSubmit={(event) => {
            event.preventDefault()
            void handleRenameSubmit()
          }}
        >
          <Input
            ref={renameInputRef}
            value={rename.value}
            onChange={(event) => rename.setValue(event.target.value)}
            onBlur={() => void handleRenameSubmit()}
            onKeyDown={(event) => {
              if (event.key === "Escape") rename.close()
            }}
            className="h-7 text-xs"
            disabled={renameGroup.isPending}
          />
        </form>
      ) : (
        <SectionHeader
          title={group.name}
          isCollapsed={isCollapsed}
          onToggle={onToggle}
          onNewChat={onNewChat}
          menu={
            <Dialog open={isConfirmOpen} onOpenChange={setIsConfirmOpen}>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`${group.name} options`}
                    className="opacity-0 focus-visible:opacity-100 group-hover/section:opacity-100"
                  >
                    <MoreHorizontal className="size-3.5" aria-hidden="true" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem onSelect={() => rename.open(group.name)}>
                    Rename
                  </DropdownMenuItem>
                  <DropdownMenuItem variant="destructive" onSelect={() => setIsConfirmOpen(true)}>
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Delete &ldquo;{group.name}&rdquo;?</DialogTitle>
                  <DialogDescription>
                    The group is removed, but its chats and documents are not deleted — they move to
                    Ungrouped. This affects {chatCountPreview} chat(s)
                    {documentCountPreview !== undefined
                      ? ` and ${documentCountPreview} document(s)`
                      : " and any documents in this group"}
                    .
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={() => setIsConfirmOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    onClick={() => void handleConfirmDelete()}
                    disabled={deleteGroup.isPending}
                  >
                    {deleteGroup.isPending ? "Deleting…" : "Delete"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          }
        />
      )}

      {!isCollapsed && (
        <div className="mt-0.5 flex flex-col gap-1 pl-4">
          {conversations.length === 0 && (
            <p className="px-3 py-1 text-xs text-sidebar-foreground/50">No chats here yet.</p>
          )}
          {conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              id={conversation.id}
              title={conversation.title}
              groupId={conversation.group_id}
              groups={groups}
              isActive={conversation.id === activeConversationId}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  )
}
