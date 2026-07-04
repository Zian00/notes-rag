import { useId, useRef, useState, type ChangeEvent, type DragEvent } from "react"
import { UploadCloud, FileText, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { formatFileSize } from "@/lib/format"

// Server-sniffed content types, not client-enforced — the `accept` attribute is
// only a picker convenience (narrows what the OS file dialog shows by default).
// The server still validates the actual bytes, so we deliberately do not
// reject files client-side based on extension or MIME type.
const ACCEPTED_FILE_TYPES = ".pdf,.pptx,.docx,.txt,.md,.png,.jpg,.jpeg"

interface UploadDropzoneProps {
  file: File | null
  onFileSelect: (file: File | null) => void
  disabled?: boolean
}

// Drag-and-drop + click-to-browse file picker. Keyboard-operable: the zone is a
// real <label> wrapping a visually-hidden, focusable <input type="file">, so
// clicking the label natively activates the input exactly once — no JS
// `.click()` call, so no risk of the synthesized click bubbling back into a
// wrapping element's own click handler and re-opening the picker.
export function UploadDropzone({ file, onFileSelect, disabled = false }: UploadDropzoneProps) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null
    onFileSelect(selected)
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    // Browsers navigate to the dropped file by default unless this is prevented.
    event.preventDefault()
    setIsDragOver(false)
    if (disabled) return
    const dropped = event.dataTransfer.files?.[0] ?? null
    onFileSelect(dropped)
  }

  function handleDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    if (!disabled) setIsDragOver(true)
  }

  function handleDragLeave(event: DragEvent<HTMLLabelElement>) {
    // Ignore dragleave fired when moving onto a child element — only clear the
    // highlight when the pointer actually leaves the drop zone.
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
    setIsDragOver(false)
  }

  function clearFile() {
    onFileSelect(null)
    if (inputRef.current) inputRef.current.value = ""
  }

  if (file) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/30 px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{file.name}</p>
            <p className="text-xs text-muted-foreground">{formatFileSize(file.size)}</p>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Remove selected file"
          onClick={clearFile}
          disabled={disabled}
        >
          <X className="size-4" />
        </Button>
      </div>
    )
  }

  return (
    <label
      htmlFor={inputId}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        "flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border px-4 py-8 text-center transition-colors",
        "hover:border-primary hover:bg-accent",
        "has-[input:focus-visible]:ring-3 has-[input:focus-visible]:ring-ring/50",
        isDragOver && "border-primary bg-accent",
        disabled && "pointer-events-none opacity-50",
      )}
    >
      <UploadCloud className="size-6 text-muted-foreground" aria-hidden="true" />
      <span className="text-sm font-medium">Drag & drop a file, or click to browse</span>
      <span className="text-xs text-muted-foreground">PDF, PPTX, DOCX, TXT, MD, PNG, JPEG</span>
      <span className="sr-only">Choose file</span>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={ACCEPTED_FILE_TYPES}
        onChange={handleChange}
        disabled={disabled}
        className="sr-only"
        tabIndex={0}
      />
    </label>
  )
}
