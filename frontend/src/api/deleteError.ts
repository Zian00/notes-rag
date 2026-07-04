// Carries the backend HTTP status so the documents UI can branch — notably a 404
// (already deleted / not owned) is treated as "already gone" rather than a hard error.
export class DeleteError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = "DeleteError"
    this.status = status
  }
}
