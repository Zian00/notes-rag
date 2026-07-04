import { useId } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export interface MetadataValues {
  title: string
  course: string
  tags: string
}

interface MetadataFieldsProps {
  values: MetadataValues
  onChange: (values: MetadataValues) => void
  disabled?: boolean
}

// Optional per-upload metadata: title/course are plain strings, tags is a
// comma-separated string in the UI that the caller splits into string[]
// (see parseTags) only at submit time — keeping raw text here avoids fighting
// the user mid-keystroke over trimming/splitting.
export function MetadataFields({ values, onChange, disabled = false }: MetadataFieldsProps) {
  const titleId = useId()
  const courseId = useId()
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
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={courseId}>Course</Label>
        <Input
          id={courseId}
          placeholder="Optional"
          value={values.course}
          disabled={disabled}
          onChange={(e) => onChange({ ...values, course: e.target.value })}
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
