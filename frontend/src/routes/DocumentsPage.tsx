import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { UploadDropzone } from "@/components/documents/UploadDropzone"
import { MetadataFields, type MetadataValues } from "@/components/documents/MetadataFields"
import { DocumentList } from "@/components/documents/DocumentList"
import { useUploadDocument } from "@/api/hooks/useDocuments"
import { UploadError } from "@/api/uploadError"
import { parseTags } from "@/lib/format"

const EMPTY_METADATA: MetadataValues = { title: "", groupId: null, tags: "" }

// Maps a failed upload's HTTP status to a message a user can act on, per the
// backend's documented error cases (400 unsupported/empty, 413 too large,
// 409 duplicate). Anything else (network error, 5xx) gets a generic fallback.
function messageForUploadError(error: UploadError): string {
  switch (error.status) {
    case 400:
      return "That file type isn't supported, or the file is empty."
    case 413:
      return "File is too large (max 25 MB)."
    case 409:
      return "You've already uploaded this file."
    default:
      return "Upload failed. Please try again."
  }
}

export function DocumentsPage() {
  const [file, setFile] = useState<File | null>(null)
  const [metadata, setMetadata] = useState<MetadataValues>(EMPTY_METADATA)
  const uploadDocument = useUploadDocument()

  async function handleUpload() {
    if (!file) return

    try {
      await uploadDocument.mutateAsync({
        file,
        title: metadata.title.trim() || undefined,
        groupId: metadata.groupId ?? undefined,
        tags: parseTags(metadata.tags),
      })
      toast.success(`Uploaded "${file.name}".`)
      setFile(null)
      setMetadata(EMPTY_METADATA)
    } catch (error) {
      // Non-UploadError throwables (e.g. a thrown network TypeError) still need
      // a user-facing message rather than surfacing nothing.
      const message =
        error instanceof UploadError
          ? messageForUploadError(error)
          : "Upload failed. Please try again."
      toast.error(message)
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <header>
        <h1 className="font-heading text-xl font-semibold">Documents</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Upload your notes to make them searchable and citable in chat.
        </p>
      </header>

      <section className="mt-6 flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
        <UploadDropzone file={file} onFileSelect={setFile} disabled={uploadDocument.isPending} />
        <MetadataFields
          values={metadata}
          onChange={setMetadata}
          disabled={uploadDocument.isPending}
        />
        <div className="flex justify-end">
          <Button type="button" onClick={handleUpload} disabled={!file || uploadDocument.isPending}>
            {uploadDocument.isPending ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </section>

      <section className="mt-8">
        <h2 className="font-heading text-sm font-semibold text-muted-foreground">Your documents</h2>
        <div className="mt-3">
          <DocumentList />
        </div>
      </section>
    </div>
  )
}
