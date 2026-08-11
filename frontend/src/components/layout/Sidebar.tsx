import { useEffect, useRef, useState, type ReactNode } from "react"
import { NavLink, useNavigate, useParams } from "react-router-dom"
import {
  ChevronDown,
  FileText,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useAuth } from "@/auth/useAuth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
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
import { useConversations, useDeleteConversation } from "@/api/hooks/useConversations"
import { useCreateGroup, useDeleteGroup, useGroups, useRenameGroup } from "@/api/hooks/useGroups"
import { $api } from "@/api/client"
import { DeleteError } from "@/api/deleteError"
import { ThemeToggle } from "@/components/layout/ThemeToggle"
import type { components } from "@/api/schema"

type ConversationResponse = components["schemas"]["ConversationResponse"]
type GroupResponse = components["schemas"]["GroupResponse"]

const NAV_ITEMS = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/documents", label: "Documents", icon: FileText },
] as const

// Sentinel id for the always-present "Ungrouped" section, distinct from any
// real group's UUID — used as a collapse-state key and nothing else (it's
// never sent to the backend; ungrouped chats are identified by group_id: null).
const UNGROUPED_SECTION_ID = "__ungrouped__"

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

  const [isCreatingGroup, setIsCreatingGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState("")
  const newGroupInputRef = useRef<HTMLInputElement>(null)

  // Focus via ref/effect rather than the `autoFocus` prop (a11y lint forbids it) —
  // fires only when the inline create form actually opens.
  useEffect(() => {
    if (isCreatingGroup) newGroupInputRef.current?.focus()
  }, [isCreatingGroup])
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
    const name = newGroupName.trim()
    if (!name) return
    try {
      await createGroup.mutateAsync(name)
      setNewGroupName("")
      setIsCreatingGroup(false)
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
                  : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
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
            onClick={() => setIsCreatingGroup(true)}
          >
            <Plus className="size-3.5" aria-hidden="true" />
          </Button>
        </div>

        {isCreatingGroup && (
          <form
            className="px-3 pb-1"
            onSubmit={(event) => {
              event.preventDefault()
              void handleCreateGroup()
            }}
          >
            <Input
              ref={newGroupInputRef}
              value={newGroupName}
              onChange={(event) => setNewGroupName(event.target.value)}
              onBlur={() => {
                if (!newGroupName.trim()) setIsCreatingGroup(false)
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setNewGroupName("")
                  setIsCreatingGroup(false)
                }
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
  )
}

interface SectionHeaderProps {
  title: string
  isCollapsed: boolean
  onToggle: () => void
  onNewChat: () => void
  menu?: ReactNode
}

function SectionHeader({ title, isCollapsed, onToggle, onNewChat, menu }: SectionHeaderProps) {
  return (
    <div className="group/section flex items-center gap-1 px-1">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!isCollapsed}
        className="flex flex-1 items-center gap-1.5 rounded px-2 py-1 text-left text-xs font-medium text-sidebar-foreground/70 hover:text-sidebar-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-sidebar-ring/50"
      >
        <ChevronDown
          className={cn("size-3.5 shrink-0 transition-transform", isCollapsed && "-rotate-90")}
          aria-hidden="true"
        />
        <span className="truncate">{title}</span>
      </button>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label={`Start chat in ${title}`}
        className="opacity-0 focus-visible:opacity-100 group-hover/section:opacity-100"
        onClick={onNewChat}
      >
        <Plus className="size-3.5" aria-hidden="true" />
      </Button>
      {menu}
    </div>
  )
}

interface GroupSectionProps {
  group: GroupResponse
  conversations: ConversationResponse[]
  activeConversationId: string | undefined
  isCollapsed: boolean
  onToggle: () => void
  onNewChat: () => void
  onNavigate?: () => void
}

// Owns its own rename/delete UI state independently of sibling sections —
// opening one group's rename input or delete-confirm dialog must not affect
// any other group's.
function GroupSection({
  group,
  conversations,
  activeConversationId,
  isCollapsed,
  onToggle,
  onNewChat,
  onNavigate,
}: GroupSectionProps) {
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(group.name)
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const renameGroup = useRenameGroup()
  const deleteGroup = useDeleteGroup()
  const renameInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isRenaming) renameInputRef.current?.focus()
  }, [isRenaming])

  // Fetched only while the delete-confirm dialog is open — this is a
  // best-effort preview count for the confirmation copy, not a proactive
  // background subscription; the backend's DELETE response (used for the
  // success toast) is the source of truth.
  const documentsPreviewQuery = $api.useQuery(
    "get",
    "/documents",
    { params: { query: { group_id: group.id } } },
    { enabled: isConfirmOpen },
  )

  async function handleRenameSubmit() {
    const name = renameValue.trim()
    if (!name || name === group.name) {
      setIsRenaming(false)
      setRenameValue(group.name)
      return
    }
    try {
      await renameGroup.mutateAsync({ groupId: group.id, name })
      setIsRenaming(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to rename group.")
    }
  }

  async function handleConfirmDelete() {
    try {
      const result = await deleteGroup.mutateAsync(group.id)
      setIsConfirmOpen(false)
      toast.success(
        `Deleted "${group.name}". ${result.chats_ungrouped} chat(s) and ${result.documents_ungrouped} document(s) moved to Ungrouped.`,
      )
    } catch {
      toast.error("Failed to delete group.")
    }
  }

  const chatCountPreview = conversations.length
  const documentCountPreview = documentsPreviewQuery.data?.length

  return (
    <div className="mt-2">
      {isRenaming ? (
        <form
          className="px-1"
          onSubmit={(event) => {
            event.preventDefault()
            void handleRenameSubmit()
          }}
        >
          <Input
            ref={renameInputRef}
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            onBlur={() => void handleRenameSubmit()}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setRenameValue(group.name)
                setIsRenaming(false)
              }
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
                  <DropdownMenuItem
                    onSelect={() => {
                      setRenameValue(group.name)
                      setIsRenaming(true)
                    }}
                  >
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
                    The group is removed, but its chats and documents are not deleted — they move
                    to Ungrouped. This affects {chatCountPreview} chat(s)
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
        <div className="mt-0.5 flex flex-col gap-1">
          {conversations.length === 0 && (
            <p className="px-4 py-1 text-xs text-sidebar-foreground/50">No chats here yet.</p>
          )}
          {conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              id={conversation.id}
              title={conversation.title}
              isActive={conversation.id === activeConversationId}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface UngroupedSectionProps {
  conversations: ConversationResponse[]
  activeConversationId: string | undefined
  isCollapsed: boolean
  onToggle: () => void
  onNavigate?: () => void
}

// The Ungrouped section always exists (it's where every chat starts and where
// a deleted group's chats land) — unlike a real group it has no rename/delete,
// so it doesn't need GroupSection's mutation/dialog machinery at all.
function UngroupedSection({
  conversations,
  activeConversationId,
  isCollapsed,
  onToggle,
  onNavigate,
}: UngroupedSectionProps) {
  const navigate = useNavigate()

  function handleNewChat() {
    navigate("/chat", { state: { newChatNonce: crypto.randomUUID() } })
    onNavigate?.()
  }

  return (
    <div className="mt-2">
      <SectionHeader
        title="Ungrouped"
        isCollapsed={isCollapsed}
        onToggle={onToggle}
        onNewChat={handleNewChat}
      />
      {!isCollapsed && (
        <div className="mt-0.5 flex flex-col gap-1">
          {conversations.length === 0 && (
            <p className="px-4 py-1 text-xs text-sidebar-foreground/50">No chats here yet.</p>
          )}
          {conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              id={conversation.id}
              title={conversation.title}
              isActive={conversation.id === activeConversationId}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface ConversationItemProps {
  id: string
  title: string | null
  isActive: boolean
  onNavigate?: () => void
}

// Split out so each row owns its own delete-confirm dialog state independently
// of its siblings (opening one row's dialog must not affect any other row).
function ConversationItem({ id, title, isActive, onNavigate }: ConversationItemProps) {
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const navigate = useNavigate()
  const deleteConversation = useDeleteConversation()

  const displayTitle = title ?? "New conversation"

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

  return (
    <div className="group/item relative">
      <NavLink
        to={`/chat/${id}`}
        onClick={onNavigate}
        className={cn(
          "flex items-center gap-2 rounded-lg px-3 py-2 pr-8 text-sm transition-colors",
          "focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-sidebar-ring/50",
          isActive
            ? "bg-sidebar-accent text-sidebar-accent-foreground"
            : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
        )}
      >
        <span className="truncate" title={displayTitle}>
          {displayTitle}
        </span>
      </NavLink>

      <Dialog open={isConfirmOpen} onOpenChange={setIsConfirmOpen}>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={`Delete ${displayTitle}`}
          className="absolute top-1/2 right-1.5 -translate-y-1/2 opacity-0 focus-visible:opacity-100 group-hover/item:opacity-100"
          onClick={() => setIsConfirmOpen(true)}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
        </Button>
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
