import { useState, type FormEvent } from "react"
import { Link, useLocation, useNavigate, type Location } from "react-router-dom"
import { toast } from "sonner"
import { useAuth } from "@/auth/useAuth"
import { AuthError } from "@/api/authError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { EMAIL_PATTERN } from "@/lib/validation"

interface FieldErrors {
  email?: string
  password?: string
}

// Maps a login AuthError's HTTP status to a user-facing message. Kept as a
// pure function (not inline in the handler) so the status->copy mapping is
// easy to scan and to unit-test in isolation if needed later.
function loginErrorMessage(error: AuthError): string {
  switch (error.status) {
    case 401:
      return "Invalid email or password."
    case 422:
      return "Please check your details and try again."
    case 0:
      return error.message
    default:
      return "Something went wrong. Please try again."
  }
}

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  function validate(): boolean {
    const errors: FieldErrors = {}
    if (!email.trim()) errors.email = "Email is required."
    else if (!EMAIL_PATTERN.test(email)) errors.email = "Enter a valid email address."
    if (!password) errors.password = "Password is required."
    setFieldErrors(errors)
    return Object.keys(errors).length === 0
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!validate()) return

    setIsSubmitting(true)
    try {
      await login(email, password)
      // ProtectedRoute stashes the originally-requested location in state.from
      // (see src/routes/ProtectedRoute.tsx) so a deep link survives the login detour.
      // Reconstruct pathname+search+hash (not just pathname) so query params and
      // in-page anchors on the deep link aren't silently dropped after login.
      const fromLocation = (location.state as { from?: Location } | null)?.from
      const from = fromLocation
        ? `${fromLocation.pathname}${fromLocation.search ?? ""}${fromLocation.hash ?? ""}`
        : "/chat"
      navigate(from, { replace: true })
    } catch (error) {
      if (error instanceof AuthError) {
        toast.error(loginErrorMessage(error))
      } else {
        toast.error("Something went wrong. Please try again.")
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-background px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          {/* Renders the card's title directly as the page's single <h1> (skipping
              CardTitle's own div wrapper) instead of nesting a heading inside a
              non-semantic title element — CardTitle doesn't support `asChild`. */}
          <h1 className="font-heading text-base leading-snug font-medium">Log in</h1>
          <CardDescription>Sign in to your Notes RAG account.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={(e) => void handleSubmit(e)} noValidate>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="login-email">Email</Label>
              <Input
                id="login-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={Boolean(fieldErrors.email)}
                aria-describedby={fieldErrors.email ? "login-email-error" : undefined}
              />
              {fieldErrors.email && (
                <p id="login-email-error" className="text-sm text-destructive" role="alert">
                  {fieldErrors.email}
                </p>
              )}
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="login-password">Password</Label>
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={Boolean(fieldErrors.password)}
                aria-describedby={fieldErrors.password ? "login-password-error" : undefined}
              />
              {fieldErrors.password && (
                <p id="login-password-error" className="text-sm text-destructive" role="alert">
                  {fieldErrors.password}
                </p>
              )}
            </div>

            <Button type="submit" className="mt-2 w-full" disabled={isSubmitting}>
              {isSubmitting ? "Logging in…" : "Log in"}
            </Button>
          </form>

          <p className="mt-4 text-center text-sm text-muted-foreground">
            Need an account?{" "}
            <Link to="/register" className="font-medium text-primary underline-offset-4 hover:underline">
              Register
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
