import { useContext } from "react"
import { AuthContext, type AuthContextValue } from "@/auth/AuthContext"

// Throws instead of returning a possibly-undefined value so a consumer
// rendered outside <AuthProvider> fails loudly at the call site, not with a
// confusing "cannot read property of undefined" deeper in the component.
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider")
  }
  return context
}
