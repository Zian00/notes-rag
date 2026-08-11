import { useId } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { GroupSelect } from "@/components/documents/GroupSelect"

export interface MetadataValues {
  title: string
  groupId: string | null
  tags: string
}

interface MetadataFieldsProps {
  values: MetadataValues
  onChange: (values: MetadataValues) => void
  disabled?: boolean
  // Forwarded to GroupSelect — see its own doc comment. Callers with an
  // adjacent submit action (DocumentsPage's Upload button) must gate on this.
  onGroupBusyChange?: (busy: boolean) => void
}

// Optional per-upload metadata: title is a plain string, tags is a
// comma-separated string in the UI that the caller splits into string[]
// (see parseTags) only at submit time — keeping raw text here avoids fighting
// the user mid-keystroke over trimming/splitting. Group is a real FK (picked
// via GroupSelect's dropdown), not free text like the old `course` field.
export function MetadataFields({
  values,
  onChange,
  disabled = false,
  onGroupBusyChange,
}: MetadataFieldsProps) {
  const titleId = useId()
  const tagsId = useId()

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={titleId}>Title</Label>
        <Input
          id={titleId}
          placeholder="Optional"
          value={values.title}
          disabled={disabled}
          onChange={(e) => onChange({ ...values, title: e.target.value })}
        />
      </div>
      <GroupSelect
        label="Group"
        value={values.groupId}
        onChange={(groupId) => onChange({ ...values, groupId })}
        disabled={disabled}
        onBusyChange={onGroupBusyChange}
      />
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={tagsId}>Tags</Label>
        <Input
          id={tagsId}
          placeholder="comma, separated"
          value={values.tags}
          disabled={disabled}
          onChange={(e) => onChange({ ...values, tags: e.target.value })}
        />
      </div>
    </div>
  )
}
