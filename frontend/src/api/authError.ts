// Carries the backend HTTP status so UI (login/register forms) can branch on it
// (401 = bad credentials, 409 = email exists, 422 = validation) without re-calling the API.
export class AuthError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "AuthError"
    this.status = status
  }
}
