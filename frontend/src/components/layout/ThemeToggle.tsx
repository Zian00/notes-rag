import { Monitor, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

// One icon per next-themes `theme` value ("system" is next-themes' own resolved-
// preference option, not a fourth OS setting) — used both for the trigger button
// (reflects the current choice) and each menu item (so all three are visible at once).
const THEME_ICONS = {
  light: Sun,
  dark: Moon,
  system: Monitor,
} as const

type ThemeChoice = keyof typeof THEME_ICONS

const THEME_LABELS: Record<ThemeChoice, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
}

// Sidebar-footer control for picking light/dark/system. Reads/writes next-themes'
// `theme` (the user's chosen preference, e.g. "system") rather than `resolvedTheme`
// (the actual light/dark result) since the trigger icon should reflect the user's
// choice, not silently relabel "system" as whichever it currently resolves to.
export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const current = (theme as ThemeChoice | undefined) ?? "system"
  const CurrentIcon = THEME_ICONS[current]

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={`Theme: ${THEME_LABELS[current]}. Change theme`}
          className="w-full justify-start gap-2 text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          <CurrentIcon className="size-4" aria-hidden="true" />
          {THEME_LABELS[current]}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {(Object.keys(THEME_ICONS) as ThemeChoice[]).map((choice) => {
          const Icon = THEME_ICONS[choice]
          return (
            <DropdownMenuItem key={choice} onSelect={() => setTheme(choice)}>
              <Icon className="size-4" aria-hidden="true" />
              {THEME_LABELS[choice]}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
