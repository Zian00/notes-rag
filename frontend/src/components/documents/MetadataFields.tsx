import { useId } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export interface MetadataValues {
  title: string
  tags: string
}

interface MetadataFieldsProps {
  values: MetadataValues
  onChange: (values: MetadataValues) => void
  disabled?: boolean
}

// Optional per-upload metadata: title is a plain string, tags is a
// comma-separated string in the UI that the caller splits into string[]
// (see parseTags) only at submit time — keeping raw text here avoids fighting
// the user mid-keystroke over trimming/splitting.
//
// Group assignment (T8) isn't wired up here yet — the old free-text `course`
// field was removed because the backend dropped it in favor of `group_id`
// (a real FK, not a string a user can type), and a group picker needs its own
// dropdown component, not a text Input.
export function MetadataFields({ values, onChange, disabled = false }: MetadataFieldsProps) {
  const titleId = useId()
  const tagsId = useId()

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
