import { useEffect, useRef, useState } from "react"
import { NavLink, useNavigate, useParams } from "react-router-dom"
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core"
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable"
import { FileText, LogOut, MessageSquare, Plus } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useAuth } from "@/auth/useAuth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useConversations, useUpdateConversation } from "@/api/hooks/useConversations"
import { useCreateGroup, useGroups } from "@/api/hooks/useGroups"
import { useInlineEdit } from "@/hooks/useInlineEdit"
import { ThemeToggle } from "@/components/layout/ThemeToggle"
import { GroupSection } from "@/components/layout/sidebar/GroupSection"
import { UngroupedSection } from "@/components/layout/sidebar/UngroupedSection"
import { UNGROUPED_SECTION_ID } from "@/components/layout/sidebar/constants"
import { resolveDropTarget } from "@/components/layout/sidebar/dragDrop"
import type { components } from "@/api/schema"

type ConversationResponse = components["schemas"]["ConversationResponse"]

const NAV_ITEMS = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/documents", label: "Documents", icon: FileText },
] as const

interface SidebarProps {
  // Lets AppShell close the mobile drawer after a nav link is clicked.
  onNavigate?: () => void
}

export function Sidebar({ onNavigate }: SidebarProps) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const { conversationId: activeConversationId } = useParams<{ conversationId?: string }>()
  const conversationsQuery = useConversations()
  const groupsQuery = useGroups()
  const createGroup = useCreateGroup()
  const groupCreate = useInlineEdit()
  const groupCreateInputRef = useRef<HTMLInputElement>(null)
  const updateConversation = useUpdateConversation()

  // Title of the chat currently being dragged, shown in the DragOverlay
  // that follows the cursor — null when no drag is in progress. Sourced
  // from active.data.current at drag start rather than re-deriving it in
  // onDragEnd, since the dragged row may unmount before the drop resolves.
  const [draggedTitle, setDraggedTitle] = useState<string | null>(null)

  const dndSensors = useSensors(
    // A small movement threshold before a drag activates, so a plain click
    // on a chat row (to navigate) or its "..." menu still works — only a
    // deliberate drag beyond this distance starts one.
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  function handleDragStart(event: DragStartEvent) {
    const conversation = conversations.find((c) => c.id === event.active.id)
    setDraggedTitle(conversation?.title ?? "New conversation")
  }

  async function handleDragEnd(event: DragEndEvent) {
    setDraggedTitle(null)
    const { active, over } = event
    if (!over) return
    const currentGroupId = (active.data.current?.groupId as string | null) ?? null
    const target = resolveDropTarget(currentGroupId, String(over.id))
    if (!target) return
    try {
      await updateConversation.mutateAsync({ id: String(active.id), groupId: target.groupId })
      toast.success("Chat moved.")
    } catch {
      toast.error("Failed to move chat.")
    }
  }

  useEffect(() => {
    if (groupCreate.isEditing) groupCreateInputRef.current?.focus()
  }, [groupCreate.isEditing])

  // Collapsed section ids (group id, or UNGROUPED_SECTION_ID) — absence from
  // this set means expanded, so a brand-new group starts expanded by default.
  const [collapsedSectionIds, setCollapsedSectionIds] = useState<Set<string>>(new Set())

  function toggleSection(sectionId: string) {
    setCollapsedSectionIds((prev) => {
      const next = new Set(prev)
      if (next.has(sectionId)) {
        next.delete(sectionId)
      } else {
        next.add(sectionId)
      }
      return next
    })
  }

  async function handleLogout() {
    await logout()
    navigate("/login", { replace: true })
  }

  // `groupId: undefined` means the top-level, ungrouped "New chat" — a group
  // section's own "+" passes its group's id so the first message pre-assigns it.
  function handleNewChat(groupId?: string) {
    // Navigating to bare /chat is a route no-op when already there (e.g. a
    // lingering live thread from a pre-`meta` error), so a fresh nonce is
    // stamped into location.state on every click. ChatPage watches this value
    // (not just the route param) and calls its own `reset()` whenever it
    // changes — including when the pathname didn't. Sidebar and ChatPage
    // don't share a useChat instance, so this state-based signal is how the
    // click reaches ChatPage's hook without lifting state up.
    navigate("/chat", { state: { newChatNonce: crypto.randomUUID(), groupId } })
    onNavigate?.()
  }

  async function handleCreateGroup() {
    const name = groupCreate.value.trim()
    if (!name) return
    try {
      await createGroup.mutateAsync(name)
      groupCreate.close()
    } catch {
      toast.error("Failed to create group.")
    }
  }

  const conversations = conversationsQuery.data ?? []
  const groups = groupsQuery.data ?? []
  const conversationsByGroup = new Map<string, ConversationResponse[]>()
  const ungroupedConversations: ConversationResponse[] = []
  for (const conversation of conversations) {
    if (conversation.group_id) {
      const bucket = conversationsByGroup.get(conversation.group_id) ?? []
      bucket.push(conversation)
      conversationsByGroup.set(conversation.group_id, bucket)
    } else {
      ungroupedConversations.push(conversation)
    }
  }

  const isLoading = conversationsQuery.isLoading || groupsQuery.isLoading

  return (
    <DndContext
      sensors={dndSensors}
      onDragStart={handleDragStart}
      onDragEnd={(e) => void handleDragEnd(e)}
    >
      <div className="flex h-full w-full flex-col bg-sidebar text-sidebar-foreground">
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-sidebar-border px-4">
          <span className="font-heading text-base font-semibold">Notes RAG</span>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label="Primary">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-sidebar-ring/50",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                )
              }
            >
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </NavLink>
          ))}

          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-3 w-full justify-start gap-2 text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            onClick={() => handleNewChat()}
          >
            <Plus className="size-4" aria-hidden="true" />
            New chat
          </Button>

          <div className="flex items-center justify-between px-3 pt-4 pb-1">
            <p className="text-xs font-medium text-sidebar-foreground/50">Groups</p>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="New group"
              onClick={() => groupCreate.open()}
            >
              <Plus className="size-3.5" aria-hidden="true" />
            </Button>
          </div>

          {groupCreate.isEditing && (
            <form
              className="px-3 pb-1"
              onSubmit={(event) => {
                event.preventDefault()
                void handleCreateGroup()
              }}
            >
              <Input
                ref={groupCreateInputRef}
                value={groupCreate.value}
                onChange={(event) => groupCreate.setValue(event.target.value)}
                onBlur={() => {
                  if (!groupCreate.value.trim()) groupCreate.close()
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") groupCreate.close()
                }}
                placeholder="Group name"
                className="h-8 text-sm"
                disabled={createGroup.isPending}
              />
            </form>
          )}

          {isLoading && (
            <div className="flex flex-col gap-1.5 px-3 py-1">
              <Skeleton className="h-7 w-full" />
              <Skeleton className="h-7 w-full" />
              <Skeleton className="h-7 w-4/5" />
            </div>
          )}

          {!isLoading &&
            groups.map((group) => (
              <GroupSection
                key={group.id}
                group={group}
                conversations={conversationsByGroup.get(group.id) ?? []}
                groups={groups}
                activeConversationId={activeConversationId}
                isCollapsed={collapsedSectionIds.has(group.id)}
                onToggle={() => toggleSection(group.id)}
                onNewChat={() => handleNewChat(group.id)}
                onNavigate={onNavigate}
              />
            ))}

          {!isLoading && (
            <UngroupedSection
              conversations={ungroupedConversations}
              groups={groups}
              activeConversationId={activeConversationId}
              isCollapsed={collapsedSectionIds.has(UNGROUPED_SECTION_ID)}
              onToggle={() => toggleSection(UNGROUPED_SECTION_ID)}
              onNavigate={onNavigate}
            />
          )}
        </nav>

        <div className="border-t border-sidebar-border p-3">
          <p className="truncate px-3 pb-2 text-xs text-sidebar-foreground/70" title={user?.email}>
            {user?.email}
          </p>
          <ThemeToggle />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-1 w-full justify-start gap-2 text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            onClick={() => void handleLogout()}
          >
            <LogOut className="size-4" aria-hidden="true" />
            Log out
          </Button>
        </div>
      </div>
      <DragOverlay>
        {draggedTitle && (
          <div className="rounded-lg bg-sidebar-accent px-3 py-2 text-sm text-sidebar-accent-foreground shadow-md">
            {draggedTitle}
          </div>
        )}
      </DragOverlay>
    </DndContext>
  )
}
