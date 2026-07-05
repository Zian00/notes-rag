import { useState } from "react"
import { NavLink, useNavigate, useParams } from "react-router-dom"
import { FileText, LogOut, MessageSquare, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useAuth } from "@/auth/useAuth"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useConversations, useDeleteConversation } from "@/api/hooks/useConversations"
import { DeleteError } from "@/api/deleteError"
import { ThemeToggle } from "@/components/layout/ThemeToggle"

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

  async function handleLogout() {
    await logout()
    navigate("/login", { replace: true })
  }

  function handleNewChat() {
    // Navigating to bare /chat is a route no-op when already there (e.g. a
    // lingering live thread from a pre-`meta` error), so a fresh nonce is
    // stamped into location.state on every click. ChatPage watches this value
    // (not just the route param) and calls its own `reset()` whenever it
    // changes — including when the pathname didn't. Sidebar and ChatPage
    // don't share a useChat instance, so this state-based signal is how the
    // click reaches ChatPage's hook without lifting state up.
    navigate("/chat", { state: { newChatNonce: crypto.randomUUID() } })
    onNavigate?.()
  }

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
          onClick={handleNewChat}
        >
          <Plus className="size-4" aria-hidden="true" />
          New chat
        </Button>

        <p className="px-3 pt-4 pb-1 text-xs font-medium text-sidebar-foreground/50">Conversations</p>

        {conversationsQuery.isLoading && (
          <div className="flex flex-col gap-1.5 px-3 py-1">
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-4/5" />
          </div>
        )}

        {!conversationsQuery.isLoading && conversationsQuery.data?.length === 0 && (
          <p className="px-3 py-1 text-xs text-sidebar-foreground/50">No conversations yet.</p>
        )}

        {conversationsQuery.data?.map((conversation) => (
          <ConversationItem
            key={conversation.id}
            id={conversation.id}
            title={conversation.title}
            isActive={conversation.id === activeConversationId}
            onNavigate={onNavigate}
          />
        ))}
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
