import { useState } from "react"
import { Outlet } from "react-router-dom"
import { Menu, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Sidebar } from "@/components/layout/Sidebar"

// Two-pane shell for authed routes: a fixed sidebar on desktop, collapsing
// into a header + toggled drawer on narrow screens. Rendered by ProtectedRoute
// once status === "authed", so children can assume a signed-in user exists.
export function AppShell() {
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false)

  return (
    <div className="flex h-svh bg-background text-foreground">
      {/* Desktop sidebar: fixed width, always visible at md+ */}
      <aside className="hidden w-64 shrink-0 border-r border-sidebar-border md:block">
        <Sidebar />
      </aside>

      {/* Mobile drawer: rendered on top only while open, dismissed on backdrop
          click or after a nav link is followed. */}
      {isMobileNavOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            className="absolute inset-0 bg-foreground/20"
            onClick={() => setIsMobileNavOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-64 shadow-lg">
            <Sidebar onNavigate={() => setIsMobileNavOpen(false)} />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile header bar: only the menu toggle is needed at md+ since the
            sidebar is already visible there. */}
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4 md:hidden">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={isMobileNavOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={isMobileNavOpen}
            onClick={() => setIsMobileNavOpen((open) => !open)}
          >
            {isMobileNavOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </Button>
          <span className="font-heading text-sm font-semibold">Notes RAG</span>
        </header>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
