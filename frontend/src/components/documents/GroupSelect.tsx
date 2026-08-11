import { useEffect, useId, useRef, type ChangeEvent } from "react"
import { toast } from "sonner"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { useCreateGroup, useGroups } from "@/api/hooks/useGroups"
import { useInlineEdit } from "@/hooks/useInlineEdit"

// Sentinel <option> values distinct from any real group UUID — never sent to
// the backend, only used to interpret the native <select>'s onChange.
const NONE_VALUE = "__none__"
const NEW_GROUP_VALUE = "__new__"

interface GroupSelectProps {
  label: string
  // null = ungrouped ("None"); a real value is the group's id.
  value: string | null
  onChange: (groupId: string | null) => void
  disabled?: boolean
  // For compact contexts (e.g. a document row) where a visible field label
  // would be redundant — the label stays in the accessibility tree via sr-only.
  hideLabel?: boolean
  className?: string
  // Fires whenever the inline "+ New group…" form is open or its create
  // request is in flight. A caller with an adjacent submit action (e.g.
  // DocumentsPage's Upload button) MUST gate that action on this — otherwise
  // clicking it while a new group is still being created reads `value` before
  // handleCreateSubmit's onChange(group.id) has run, silently submitting with
  // the pre-creation (often null) group instead of the one just typed.
  onBusyChange?: (busy: boolean) => void
}

// A native <select> (not a Radix DropdownMenu) deliberately — this project's
// Sidebar hit real fragility driving a Radix DropdownMenuSub with userEvent in
// jsdom, and a native select is both simpler and trivially testable via
// userEvent.selectOptions. Choosing "+ New group..." swaps to an inline create
// form in place of the select, mirroring Sidebar's own inline-create pattern.
export function GroupSelect({
  label,
  value,
  onChange,
  disabled = false,
  hideLabel = false,
  className,
  onBusyChange,
}: GroupSelectProps) {
  const groupsQuery = useGroups()
  const createGroup = useCreateGroup()
  const create = useInlineEdit()
  const createInputRef = useRef<HTMLInputElement>(null)
  const selectId = useId()
  const isBusy = create.isEditing || createGroup.isPending

  useEffect(() => {
    if (create.isEditing) createInputRef.current?.focus()
  }, [create.isEditing])

  useEffect(() => {
    onBusyChange?.(isBusy)
  }, [isBusy, onBusyChange])

  const groups = groupsQuery.data ?? []

  function handleSelectChange(event: ChangeEvent<HTMLSelectElement>) {
    const raw = event.target.value
    if (raw === NEW_GROUP_VALUE) {
      create.open()
      return
    }
    onChange(raw === NONE_VALUE ? null : raw)
  }

  async function handleCreateSubmit() {
    const name = create.value.trim()
    if (!name) {
      create.close()
      return
    }
    try {
      const group = await createGroup.mutateAsync(name)
      onChange(group.id)
      create.close()
    } catch {
      toast.error("Failed to create group.")
    }
  }

  if (create.isEditing) {
    return (
      <div className={cn("flex flex-col gap-1.5", className)}>
        <Label htmlFor={selectId} className={cn(hideLabel && "sr-only")}>
          {label}
        </Label>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void handleCreateSubmit()
          }}
        >
          <Input
            id={selectId}
            ref={createInputRef}
            value={create.value}
            onChange={(event) => create.setValue(event.target.value)}
            onBlur={() => void handleCreateSubmit()}
            onKeyDown={(event) => {
              if (event.key === "Escape") create.close()
            }}
            placeholder="Group name"
            disabled={createGroup.isPending}
          />
        </form>
      </div>
    )
  }

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={selectId} className={cn(hideLabel && "sr-only")}>
        {label}
      </Label>
      <select
        id={selectId}
        value={value ?? NONE_VALUE}
        onChange={handleSelectChange}
        disabled={disabled}
        className={cn(
          "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none",
          "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
          "disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50",
          "md:text-sm [color-scheme:light] dark:bg-input/30 dark:[color-scheme:dark] dark:disabled:bg-input/80"
        )}
      >
        {/* Chromium's native <option> popup ignores color-scheme entirely for
            its own background/text colors (verified live: computed
            color-scheme was "dark" on both this <select> and <html>, yet the
            popup still painted light) — it DOES respect ordinary CSS on the
            <option> elements themselves, so that's what actually themes it. */}
        <option className="bg-popover text-popover-foreground" value={NONE_VALUE}>
          None
        </option>
        {groups.map((group) => (
          <option key={group.id} className="bg-popover text-popover-foreground" value={group.id}>
            {group.name}
          </option>
        ))}
        <option className="bg-popover text-popover-foreground" value={NEW_GROUP_VALUE}>
          + New group…
        </option>
      </select>
    </div>
  )
}
