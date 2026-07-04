// Carries the backend HTTP status (+ the existing doc id on a 409 duplicate) so the
// upload UI can branch: 400 unsupported/empty, 413 too large, 409 duplicate.
export class UploadError extends Error {
  readonly status: number
  readonly existingDocumentId?: string

  constructor(status: number, message: string, existingDocumentId?: string) {
    super(message)
    this.name = "UploadError"
    this.status = status
    this.existingDocumentId = existingDocumentId
  }
}
