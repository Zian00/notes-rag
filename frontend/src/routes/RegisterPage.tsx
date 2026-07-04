import { useState, type FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { useAuth } from "@/auth/useAuth"
import { AuthError } from "@/api/authError"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { EMAIL_PATTERN, MIN_PASSWORD_LENGTH } from "@/lib/validation"

interface FieldErrors {
  email?: string
  password?: string
}

// Maps a register AuthError's HTTP status to a user-facing message.
function registerErrorMessage(error: AuthError): string {
  switch (error.status) {
    case 409:
      return "That email is already registered."
    case 422:
      return "Please check your details and try again."
    case 0:
      // Register succeeded but the immediate auto-login failed — AuthContext's
      // message is already phrased for end users, so show it verbatim.
      return error.message
    default:
      return "Something went wrong. Please try again."
  }
}

export function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  function validate(): boolean {
    const errors: FieldErrors = {}
    if (!email.trim()) errors.email = "Email is required."
    else if (!EMAIL_PATTERN.test(email)) errors.email = "Enter a valid email address."
    if (!password) errors.password = "Password is required."
    else if (password.length < MIN_PASSWORD_LENGTH) {
      errors.password = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`
    }
    setFieldErrors(errors)
    return Object.keys(errors).length === 0
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!validate()) return

    setIsSubmitting(true)
    try {
      await register(email, password)
      navigate("/chat", { replace: true })
    } catch (error) {
      if (error instanceof AuthError) {
        toast.error(registerErrorMessage(error))
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
          <CardTitle>
            <h1 className="text-base font-medium">Create an account</h1>
          </CardTitle>
          <CardDescription>Get started with Notes RAG.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={(e) => void handleSubmit(e)} noValidate>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="register-email">Email</Label>
              <Input
                id="register-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={Boolean(fieldErrors.email)}
                aria-describedby={fieldErrors.email ? "register-email-error" : undefined}
              />
              {fieldErrors.email && (
                <p id="register-email-error" className="text-sm text-destructive" role="alert">
                  {fieldErrors.email}
                </p>
              )}
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="register-password">Password</Label>
              <Input
                id="register-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={Boolean(fieldErrors.password)}
                aria-describedby={fieldErrors.password ? "register-password-error" : undefined}
              />
              {fieldErrors.password && (
                <p id="register-password-error" className="text-sm text-destructive" role="alert">
                  {fieldErrors.password}
                </p>
              )}
            </div>

            <Button type="submit" className="mt-2 w-full" disabled={isSubmitting}>
              {isSubmitting ? "Creating account…" : "Create account"}
            </Button>
          </form>

          <p className="mt-4 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link to="/login" className="font-medium text-primary underline-offset-4 hover:underline">
              Log in
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
