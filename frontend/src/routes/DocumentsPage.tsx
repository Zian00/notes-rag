import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { UploadDropzone } from "@/components/documents/UploadDropzone"
import { MetadataFields, type MetadataValues } from "@/components/documents/MetadataFields"
import { DocumentList } from "@/components/documents/DocumentList"
import { DocumentGroupFilter } from "@/components/documents/DocumentGroupFilter"
import { useUploadDocument } from "@/api/hooks/useDocuments"
import { UploadError } from "@/api/uploadError"
import { messageForUploadError } from "@/lib/uploadErrorMessage"
import { parseTags } from "@/lib/format"

const EMPTY_METADATA: MetadataValues = { title: "", groupId: null, tags: "" }

// List-first layout (T11): the document list is the primary surface, upload
// is a secondary action behind a dialog — most uploads now happen from chat
// (#11/#13), so this page mainly serves browsing/filtering/managing what's
// already there.
export function DocumentsPage() {
  // Page-level layout state (T11): which section is showing, and the active filter.
  const [isUploadOpen, setIsUploadOpen] = useState(false)
  const [filterGroupId, setFilterGroupId] = useState<string | undefined>(undefined)

  // Upload dialog's own form state — same fields the old always-expanded form used.
  const [file, setFile] = useState<File | null>(null)
  const [metadata, setMetadata] = useState<MetadataValues>(EMPTY_METADATA)
  // True while MetadataFields' Group field has its inline "+ New group…" form
  // open or mid-request — Upload MUST stay disabled through this window (see
  // GroupSelect's onBusyChange doc comment): otherwise clicking Upload right
  // after typing a new group name and hitting Enter can submit before
  // metadata.groupId has picked up the just-created group, silently
  // uploading ungrouped.
  const [isGroupBusy, setIsGroupBusy] = useState(false)
  const uploadDocument = useUploadDocument()

  function resetUploadForm() {
    setFile(null)
    setMetadata(EMPTY_METADATA)
    setIsGroupBusy(false)
  }

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
      resetUploadForm()
      setIsUploadOpen(false)
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
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-heading text-xl font-semibold">Documents</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload your notes to make them searchable and citable in chat.
          </p>
        </div>
        <Dialog
          open={isUploadOpen}
          onOpenChange={(open) => {
            setIsUploadOpen(open)
            if (!open) resetUploadForm()
          }}
        >
          <Button type="button" onClick={() => setIsUploadOpen(true)}>
            + Upload
          </Button>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Upload a document</DialogTitle>
              <DialogDescription>
                Add a note to make it searchable and citable in chat.
              </DialogDescription>
            </DialogHeader>
            <UploadDropzone
              file={file}
              onFileSelect={setFile}
              disabled={uploadDocument.isPending}
            />
            <MetadataFields
              values={metadata}
              onChange={setMetadata}
              disabled={uploadDocument.isPending}
              onGroupBusyChange={setIsGroupBusy}
            />
            <DialogFooter>
              <Button
                type="button"
                onClick={() => void handleUpload()}
                disabled={!file || uploadDocument.isPending || isGroupBusy}
              >
                {uploadDocument.isPending ? "Uploading…" : "Upload"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </header>

      <section className="mt-8">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-heading text-sm font-semibold text-muted-foreground">
            Your documents
          </h2>
          <DocumentGroupFilter value={filterGroupId} onChange={setFilterGroupId} />
        </div>
        <div className="mt-3">
          <DocumentList groupId={filterGroupId} />
        </div>
      </section>
    </div>
  )
}
