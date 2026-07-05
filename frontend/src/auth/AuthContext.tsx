import { createContext, useEffect, useRef, useState, type ReactNode } from "react"
import { fetchClient, setAccessToken, setOnAuthFailure, refreshAccessToken } from "@/api/client"
import { queryClient } from "@/lib/queryClient"
import { AuthError } from "@/api/authError"
import type { components } from "@/api/schema"

export type User = components["schemas"]["UserResponse"]
export type AuthStatus = "loading" | "authed" | "anon"

export interface AuthContextValue {
  user: User | null
  status: AuthStatus
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

// undefined (not null) as the "no provider" sentinel — useAuth() checks for
// this to throw a clear error instead of silently returning a broken value.
// The task spec keeps AuthContext + AuthProvider in one file (useAuth.ts consumes
// this export), so the react-refresh "only export components" rule is disabled here.
export const AuthContext = createContext<AuthContextValue | undefined>(undefined) // eslint-disable-line react-refresh/only-export-components

interface AuthProviderProps {
  children: ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<AuthStatus>("loading")

  // React 18/19 StrictMode double-invokes effects in dev (mount, cleanup, remount)
  // to surface missing-cleanup bugs. The silent refresh must only fire once per
  // real app load, not once per StrictMode remount — a ref (not state, so it
  // doesn't trigger a re-render) survives across that double-invoke and guards it.
  const hasAttemptedSilentRefresh = useRef(false)

  useEffect(() => {
    if (hasAttemptedSilentRefresh.current) return
    hasAttemptedSilentRefresh.current = true

    async function silentRefresh() {
      const refreshed = await refreshAccessToken()
      if (!refreshed) {
        setStatus("anon")
        return
      }
      const { data, error } = await fetchClient.GET("/auth/me")
      if (error || !data) {
        setAccessToken(null)
        setStatus("anon")
        return
      }
      setUser(data)
      setStatus("authed")
    }

    void silentRefresh()
  }, [])

  useEffect(() => {
    // Fires when a later request's silent refresh fails (e.g. the refresh
    // cookie expired mid-session) — drop the stale session client-side.
    setOnAuthFailure(() => {
      setAccessToken(null)
      setUser(null)
      setStatus("anon")
      // Symmetric with logout(): this is also a "session ends" transition, so
      // drop cached documents/conversations to avoid stale, unauthorized data
      // flashing in for the next login.
      queryClient.clear()
    })
  }, [])

  async function login(email: string, password: string): Promise<void> {
    const { data, error, response } = await fetchClient.POST("/auth/login", {
      body: { email, password },
    })
    // Throw (don't swallow) on failure so the calling login form can catch it
    // and surface an error toast; only a successful login should flip status.
    // Carry the HTTP status so the login form can distinguish e.g. 401 (bad
    // credentials) from 422 (validation) without re-deriving it from the message.
    if (error || !data) {
      throw new AuthError(response.status, "Login failed")
    }
    setAccessToken(data.access_token)

    const { data: meData, error: meError, response: meResponse } = await fetchClient.GET("/auth/me")
    // Read status before narrowing on meError/meData — accessing meResponse.status
    // *inside* that branch collapses TS's inference to `never` for this schema
    // shape (an operation with only a 200 response defined, no error variants).
    const meStatus = meResponse.status
    if (meError || !meData) {
      throw new AuthError(meStatus, "Failed to load user after login")
    }
    setUser(meData)
    setStatus("authed")
  }

  async function register(email: string, password: string): Promise<void> {
    const { error, response } = await fetchClient.POST("/auth/register", {
      body: { email, password },
    })
    if (error) {
      // Carry the status so the register form can distinguish e.g. 409 (email
      // already registered) from 422 (validation).
      throw new AuthError(response.status, "Registration failed")
    }
    // Backend register does NOT set a cookie or return a token (no auto-login) —
    // log in immediately after so the user still lands in the app authed.
    try {
      await login(email, password)
    } catch {
      // Register succeeded (account exists) but the immediate auto-login failed — tell the
      // user their account was created so they retry sign-in rather than re-registering.
      throw new AuthError(0, "Account created, but automatic sign-in failed — please log in.")
    }
  }

  async function logout(): Promise<void> {
    try {
      await fetchClient.POST("/auth/logout")
    } catch {
      // Ignore network errors — logout must always proceed locally even if
      // the server call fails (e.g. offline), so the user isn't stuck "authed".
    }
    setAccessToken(null)
    setUser(null)
    setStatus("anon")
    // Drop cached documents/conversations from the previous session so the
    // next user (or a re-login) never sees stale, unauthorized data flash in.
    queryClient.clear()
  }

  const value: AuthContextValue = { user, status, login, register, logout }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
