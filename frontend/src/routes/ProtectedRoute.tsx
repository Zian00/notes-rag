import { Navigate, Outlet, useLocation } from "react-router-dom"
import { useAuth } from "@/auth/useAuth"

// Full-page loading state shown while the on-mount silent refresh is in
// flight — without this, an anon-by-default render would flash the login
// page for a moment even for users with a valid refresh cookie.
function AuthLoadingScreen() {
  return (
    <div className="flex min-h-svh items-center justify-center bg-background" role="status" aria-live="polite">
      <div className="size-8 animate-spin rounded-full border-2 border-muted border-t-primary" />
      <span className="sr-only">Loading…</span>
    </div>
  )
}

// Guards protected routes: renders children (via Outlet) only when authed,
// redirects anonymous users to /login, and shows a loading state in between
// so the silent-refresh check never causes a login-page flash.
export function ProtectedRoute() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === "loading") {
    return <AuthLoadingScreen />
  }

  if (status === "anon") {
    // Preserve the intended destination so LoginPage can send the user back
    // where they meant to go after a successful login.
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <Outlet />
}
