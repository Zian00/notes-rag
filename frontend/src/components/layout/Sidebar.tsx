import { NavLink, useNavigate } from "react-router-dom"
import { FileText, LogOut, MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"
import { useAuth } from "@/auth/useAuth"
import { Button } from "@/components/ui/button"

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

  async function handleLogout() {
    await logout()
    navigate("/login", { replace: true })
  }

  return (
    <div className="flex h-full w-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 shrink-0 items-center gap-2 border-b border-sidebar-border px-4">
        <span className="font-heading text-base font-semibold">Notes RAG</span>
      </div>

      <nav className="flex-1 space-y-1 p-3" aria-label="Primary">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
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

        {/* Conversation list is Milestone D — intentionally not built yet. */}
        <p className="px-3 pt-4 text-xs text-sidebar-foreground/50">
          Conversation history coming in Milestone D
        </p>
      </nav>

      <div className="border-t border-sidebar-border p-3">
        <p className="truncate px-3 pb-2 text-xs text-sidebar-foreground/70" title={user?.email}>
          {user?.email}
        </p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="w-full justify-start gap-2 text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          onClick={() => void handleLogout()}
        >
          <LogOut className="size-4" aria-hidden="true" />
          Log out
        </Button>
      </div>
    </div>
  )
}
