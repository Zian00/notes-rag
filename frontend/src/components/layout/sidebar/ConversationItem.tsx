import { useEffect, useRef, useState } from "react"
import { NavLink, useNavigate } from "react-router-dom"
import { useDraggable } from "@dnd-kit/core"
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
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useDeleteConversation, useUpdateConversation } from "@/api/hooks/useConversations"
import { DeleteError } from "@/api/deleteError"
import { useInlineEdit } from "@/hooks/useInlineEdit"
import type { components } from "@/api/schema"

type GroupResponse = components["schemas"]["GroupResponse"]

interface ConversationItemProps {
  id: string
  title: string | null
  groupId: string | null
  isActive: boolean
  // Sidebar already fetches the groups list once for the whole tree — passed
  // down rather than each row calling useGroups() itself, which would mean one
  // hook subscription per visible chat (React Query dedupes the network call,
  // but not the subscription) just to populate an identical "Move to" list.
  groups: GroupResponse[]
  onNavigate?: () => void
}

// Split out so each row owns its own rename/move/delete UI state independently
// of its siblings (opening one row's menu or dialog must not affect any other row).
export function ConversationItem({
  id,
  title,
  groupId,
  isActive,
  groups,
  onNavigate,
}: ConversationItemProps) {
  const rename = useInlineEdit()
  const renameInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (rename.isEditing) renameInputRef.current?.focus()
  }, [rename.isEditing])
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const navigate = useNavigate()
  const updateConversation = useUpdateConversation()
  const deleteConversation = useDeleteConversation()

  // Drag source for moving this chat into a group/Ungrouped section by drag
  // (see Sidebar's DndContext for the drop side). `data.groupId` lets
  // Sidebar's onDragEnd know the chat's CURRENT group without a separate
  // lookup — attributes/listeners go on the row's outer wrapper below, not
  // the NavLink, so plain clicks still navigate (PointerSensor requires
  // moving past a distance threshold before a drag activates).
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id,
    data: { groupId },
  })

  const displayTitle = title ?? "New conversation"

  async function handleRenameSubmit() {
    const trimmed = rename.value.trim()
    if (!trimmed || trimmed === (title ?? "")) {
      rename.close()
      return
    }
    try {
      await updateConversation.mutateAsync({ id, title: trimmed })
      rename.close()
    } catch {
      toast.error("Failed to rename conversation.")
    }
  }

  // `targetGroupId: null` moves the chat to Ungrouped — mirrors the PATCH
  // body's group_id: null semantics (see useUpdateConversation).
  async function handleMoveToGroup(targetGroupId: string | null) {
    try {
      await updateConversation.mutateAsync({ id, groupId: targetGroupId })
      toast.success("Chat moved.")
    } catch {
      toast.error("Failed to move chat.")
    }
  }

  async function handleConfirmDelete() {
    try {
      await deleteConversation.mutateAsync(id)
      toast.success("Conversation deleted.")
      setIsConfirmOpen(false)
      // The conversation the user is currently viewing was just removed —
      // navigating away avoids showing a dead thread for a now-gone id.
      if (isActive) {
        navigate("/chat")
      }
    } catch (error) {
      if (error instanceof DeleteError && error.status === 404) {
        toast.info("This conversation was already removed.")
        setIsConfirmOpen(false)
        if (isActive) navigate("/chat")
        return
      }
      toast.error("Failed to delete conversation. Please try again.")
    }
  }

  if (rename.isEditing) {
    return (
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
          className="h-8 text-sm"
          disabled={updateConversation.isPending}
        />
      </form>
    )
  }

  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      className={cn("group/item relative", isDragging && "opacity-40")}
    >
      <NavLink
        to={`/chat/${id}`}
        onClick={onNavigate}
        className={cn(
          "flex items-center gap-2 rounded-lg px-3 py-2 pr-8 text-sm transition-colors",
          "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-sidebar-ring/50",
          isActive
            ? "bg-sidebar-accent text-sidebar-accent-foreground"
            : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        )}
      >
        <span className="truncate" title={displayTitle}>
          {displayTitle}
        </span>
      </NavLink>

      <Dialog open={isConfirmOpen} onOpenChange={setIsConfirmOpen}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`${displayTitle} options`}
              className="absolute top-1/2 right-1.5 -translate-y-1/2 opacity-0 focus-visible:opacity-100 group-hover/item:opacity-100"
            >
              <MoreHorizontal className="size-3.5" aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem onSelect={() => rename.open(title ?? "")}>Rename</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Move to</DropdownMenuLabel>
            <DropdownMenuItem
              disabled={groupId === null}
              onSelect={() => void handleMoveToGroup(null)}
            >
              Ungrouped
            </DropdownMenuItem>
            {groups.map((group) => (
              <DropdownMenuItem
                key={group.id}
                disabled={group.id === groupId}
                onSelect={() => void handleMoveToGroup(group.id)}
              >
                {group.name}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={() => setIsConfirmOpen(true)}>
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription>
              This removes &ldquo;{displayTitle}&rdquo; and its messages. This can&apos;t be undone.
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
              disabled={deleteConversation.isPending}
            >
              {deleteConversation.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
