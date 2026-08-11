import type { ReactNode } from "react"
import { ChevronDown, Plus } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

interface SectionHeaderProps {
  title: string
  isCollapsed: boolean
  onToggle: () => void
  onNewChat: () => void
  menu?: ReactNode
}

// Shared collapsible-section header for both a group section and the
// Ungrouped section — the "+" (new chat in this section) and optional
// "..." menu slot are the only pieces that differ between the two.
export function SectionHeader({ title, isCollapsed, onToggle, onNewChat, menu }: SectionHeaderProps) {
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
