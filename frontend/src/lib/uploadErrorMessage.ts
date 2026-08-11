import { UploadError } from "@/api/uploadError"

// Maps a failed upload's HTTP status to a message a user can act on, per the
// backend's documented error cases (400 unsupported/empty, 413 too large,
// 409 duplicate). Anything else (network error, 5xx) gets a generic fallback.
// Shared between DocumentsPage's upload form and ChatInput's attach affordance
// (T10a) so the two upload entry points never drift on wording.
export function messageForUploadError(error: UploadError): string {
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
