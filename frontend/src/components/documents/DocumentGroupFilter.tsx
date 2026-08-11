import { useId } from "react"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { useGroups } from "@/api/hooks/useGroups"
import {
  GROUP_OPTION_CLASSNAME,
  GROUP_SELECT_CLASSNAME,
} from "@/components/documents/groupSelectStyles"

// Sentinel <option> value distinct from any real group UUID — never sent to
// the backend, only used to interpret the native <select>'s onChange as "no
// filter" (undefined groupId, matching useDocuments' own "list everything"
// default).
const ALL_VALUE = "__all__"

interface DocumentGroupFilterProps {
  value: string | undefined
  onChange: (groupId: string | undefined) => void
}

// A dropdown filter for DocumentList (T11 §7: dropdown, not Sidebar-style
// sections, for this first cut — Documents has no section concept today).
// Deliberately doesn't reuse GroupSelect: that component's "None"/"+ New
// group…" options mean "assign a group", not "filter the list", so a
// separate, simpler <select> avoids mismatched semantics.
export function DocumentGroupFilter({ value, onChange }: DocumentGroupFilterProps) {
  const groupsQuery = useGroups()
  const groups = groupsQuery.data ?? []
  const selectId = useId()

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={selectId} className="sr-only">
        Filter by group
      </Label>
      <select
        id={selectId}
        value={value ?? ALL_VALUE}
        onChange={(event) => {
          const raw = event.target.value
          onChange(raw === ALL_VALUE ? undefined : raw)
        }}
        className={cn(GROUP_SELECT_CLASSNAME, "md:w-48")}
      >
        <option className={GROUP_OPTION_CLASSNAME} value={ALL_VALUE}>
          All groups
        </option>
        {groups.map((group) => (
          <option key={group.id} className={GROUP_OPTION_CLASSNAME} value={group.id}>
            {group.name}
          </option>
        ))}
      </select>
    </div>
  )
}
