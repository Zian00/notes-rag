import { FileStack } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { DocumentRow } from "@/components/documents/DocumentRow"
import { useDocuments } from "@/api/hooks/useDocuments"

const SKELETON_ROW_COUNT = 3

export function DocumentList() {
  const { data, isLoading, error } = useDocuments()

  if (isLoading) {
    return (
      <ul className="flex flex-col gap-2" aria-label="Loading documents">
        {Array.from({ length: SKELETON_ROW_COUNT }).map((_, index) => (
          // Static placeholder rows with no identity of their own — index is a
          // stable and appropriate key here since the list never reorders.
          <li key={index}>
            <Skeleton className="h-16 w-full rounded-lg" />
          </li>
        ))}
      </ul>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        Couldn&apos;t load your documents. Please refresh and try again.
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-4 py-10 text-center">
        <FileStack className="size-8 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm font-medium">No documents yet</p>
        <p className="text-sm text-muted-foreground">Upload your first note to get started.</p>
      </div>
    )
  }

  return (
    <ul className="flex flex-col gap-2">
      {data.map((document) => (
        <DocumentRow key={document.id} document={document} />
      ))}
    </ul>
  )
}
