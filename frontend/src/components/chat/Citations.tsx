import { useState } from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Citation } from "@/api/chatStream"

interface CitationsProps {
  citations: Citation[]
}

// Citations are deduped by document server-side, not a strict 1:1 mapping to the
// answer's inline [n] markers (per Phase 3 behavior) — so this renders a plain
// "Sources" list rather than trying to line numbers up with marker positions.
export function Citations({ citations }: CitationsProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  if (citations.length === 0) return null

  return (
    <div className="mt-2 border-t border-border/60 pt-2">
      <button
        type="button"
        aria-expanded={isExpanded}
        onClick={() => setIsExpanded((expanded) => !expanded)}
        className="flex items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <ChevronDown className={cn("size-3.5 transition-transform", isExpanded && "rotate-180")} aria-hidden="true" />
        Sources
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[0.7rem] leading-none">{citations.length}</span>
      </button>

      {isExpanded && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {citations.map((citation, index) => (
            // Citations have no guaranteed unique id from the backend (chunk_id is
            // nullable) — index is safe here because this list is static per message,
            // never reordered/filtered after render.
            <li key={citation.chunk_id ?? index} className="rounded-md bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{citation.title ?? citation.filename ?? "Untitled source"}</span>
              {citation.section && <span> &middot; {citation.section}</span>}
              {typeof citation.score === "number" && <span> &middot; score {citation.score.toFixed(2)}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
