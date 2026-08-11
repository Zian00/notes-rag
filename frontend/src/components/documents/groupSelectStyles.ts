// Shared styling for the native <select>s that list groups (GroupSelect's
// assign dropdown and DocumentGroupFilter's filter dropdown) — same visual
// shape, kept in one place so they can't drift out of sync with each other.
export const GROUP_SELECT_CLASSNAME =
  "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 md:text-sm [color-scheme:light] dark:bg-input/30 dark:[color-scheme:dark] dark:disabled:bg-input/80"

// Chromium's native <option> popup ignores color-scheme entirely for its own
// background/text colors (verified live: computed color-scheme was "dark" on
// both the <select> and <html>, yet the popup still painted light) — it DOES
// respect ordinary CSS on the <option> elements themselves, so that's what
// actually themes it.
export const GROUP_OPTION_CLASSNAME = "bg-popover text-popover-foreground"
