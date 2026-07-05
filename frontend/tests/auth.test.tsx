import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { http, HttpResponse } from "msw"
import { server } from "./msw/server"
import { setAccessToken } from "@/api/client"
import * as client from "@/api/client"
import { AuthError } from "@/api/authError"
import { AuthProvider } from "@/auth/AuthContext"
import { useAuth } from "@/auth/useAuth"
import { queryClient as singletonQueryClient } from "@/lib/queryClient"

// client.ts resolves its relative "/api" base against window.location.origin —
// derive the test base the same way so the two can't drift (see tests/client.test.ts).
const API_BASE = `${window.location.origin}/api`

// Tiny consumer that surfaces the auth state as text so tests can assert on it
// via the DOM (findBy* for the async on-mount refresh transition).
function AuthStatusProbe() {
  const { user, status, login, logout } = useAuth()
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="user-email">{user?.email ?? "none"}</span>
      <button onClick={() => void login("user@example.com", "password123")}>login</button>
      <button onClick={() => void logout()}>logout</button>
    </div>
  )
}

// Fresh QueryClient per render so cache state (and queryClient.clear() calls
// inside logout) can't leak between tests.
function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AuthStatusProbe />
      </AuthProvider>
    </QueryClientProvider>,
  )
}

const mockUser = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "user@example.com",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
}

describe("AuthContext", () => {
  beforeEach(() => {
    // Reset the module-level in-memory token so tests don't leak state into each other.
    setAccessToken(null)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    // The onAuthFailure cache-clear test seeds the real singleton queryClient
    // (shared across the whole test file/process) — clear it so it can't leak
    // cached data into unrelated tests.
    singletonQueryClient.clear()
  })

  it("on-mount silent refresh success -> authed", async () => {
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () =>
        HttpResponse.json({ access_token: "t", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
    )

    renderWithProviders()

    expect(await screen.findByTestId("status")).toHaveTextContent("authed")
    expect(screen.getByTestId("user-email")).toHaveTextContent("user@example.com")
  })

  it("on-mount refresh failure -> anon", async () => {
    server.use(http.post(`${API_BASE}/auth/refresh`, () => new HttpResponse(null, { status: 401 })))

    renderWithProviders()

    expect(await screen.findByTestId("status")).toHaveTextContent("anon")
    expect(screen.getByTestId("user-email")).toHaveTextContent("none")
  })

  it("login sets user + authed", async () => {
    // On-mount refresh fails first so we start anon, then login succeeds.
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () => new HttpResponse(null, { status: 401 })),
      http.post(`${API_BASE}/auth/login`, () =>
        HttpResponse.json({ access_token: "login-token", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
    )

    renderWithProviders()
    expect(await screen.findByTestId("status")).toHaveTextContent("anon")

    screen.getByText("login").click()

    expect(await screen.findByTestId("status")).toHaveTextContent("authed")
    expect(screen.getByTestId("user-email")).toHaveTextContent("user@example.com")
  })

  it("logout clears to anon", async () => {
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () =>
        HttpResponse.json({ access_token: "t", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
      http.post(`${API_BASE}/auth/logout`, () => HttpResponse.json({ detail: "logged out" })),
    )

    renderWithProviders()
    expect(await screen.findByTestId("status")).toHaveTextContent("authed")

    screen.getByText("logout").click()

    expect(await screen.findByTestId("status")).toHaveTextContent("anon")
    expect(screen.getByTestId("user-email")).toHaveTextContent("none")
  })

  it("onAuthFailure (mid-session silent-refresh failure) clears to anon AND clears the query cache", async () => {
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () =>
        HttpResponse.json({ access_token: "t", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
    )

    // Capture the real handler AuthProvider registers (client.ts's middleware
    // calls this exact callback on an unrecoverable 401) so it can be invoked
    // directly, the same way a later mid-session request would trigger it.
    let capturedHandler: (() => void) | undefined
    vi.spyOn(client, "setOnAuthFailure").mockImplementation((fn) => {
      capturedHandler = fn
    })

    renderWithProviders()
    expect(await screen.findByTestId("status")).toHaveTextContent("authed")

    // Seed the module-level singleton queryClient (the one AuthContext's
    // onAuthFailure callback calls .clear() on) with cached data so we can
    // assert it's gone afterwards — parity with logout()'s existing clear.
    singletonQueryClient.setQueryData(["probe"], { stale: true })
    expect(singletonQueryClient.getQueryData(["probe"])).toEqual({ stale: true })

    capturedHandler?.()

    expect(await screen.findByTestId("status")).toHaveTextContent("anon")
    expect(singletonQueryClient.getQueryData(["probe"])).toBeUndefined()
  })

  it("register success -> auto-login -> authed", async () => {
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () => new HttpResponse(null, { status: 401 })),
      http.post(`${API_BASE}/auth/register`, () => HttpResponse.json(mockUser, { status: 201 })),
      http.post(`${API_BASE}/auth/login`, () =>
        HttpResponse.json({ access_token: "register-token", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
    )

    function RegisterProbe() {
      const { register, status, user } = useAuth()
      return (
        <div>
          <span data-testid="status">{status}</span>
          <span data-testid="user-email">{user?.email ?? "none"}</span>
          <button onClick={() => void register("user@example.com", "password123")}>register</button>
        </div>
      )
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RegisterProbe />
        </AuthProvider>
      </QueryClientProvider>,
    )
    expect(await screen.findByTestId("status")).toHaveTextContent("anon")

    screen.getByText("register").click()

    expect(await screen.findByTestId("status")).toHaveTextContent("authed")
    expect(screen.getByTestId("user-email")).toHaveTextContent("user@example.com")
  })

  it("login 401 rejects with AuthError carrying status 401", async () => {
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () => new HttpResponse(null, { status: 401 })),
      http.post(`${API_BASE}/auth/login`, () =>
        HttpResponse.json({ detail: "Incorrect email or password" }, { status: 401 }),
      ),
    )

    // Capture the login function + any thrown error via a probe, since the
    // button-click flow (used above) swallows the promise rejection.
    let capturedError: unknown
    function LoginErrorProbe() {
      const { login, status } = useAuth()
      return (
        <div>
          <span data-testid="status">{status}</span>
          <button
            onClick={() => {
              login("user@example.com", "wrong-password").catch((err: unknown) => {
                capturedError = err
              })
            }}
          >
            login
          </button>
        </div>
      )
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <LoginErrorProbe />
        </AuthProvider>
      </QueryClientProvider>,
    )
    expect(await screen.findByTestId("status")).toHaveTextContent("anon")

    screen.getByText("login").click()

    await screen.findByTestId("status")
    expect(capturedError).toBeInstanceOf(AuthError)
    expect((capturedError as AuthError).status).toBe(401)
  })

  it("register 409 rejects with AuthError carrying status 409", async () => {
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () => new HttpResponse(null, { status: 401 })),
      http.post(`${API_BASE}/auth/register`, () =>
        HttpResponse.json({ detail: "Email already registered" }, { status: 409 }),
      ),
    )

    let capturedError: unknown
    function RegisterErrorProbe() {
      const { register, status } = useAuth()
      return (
        <div>
          <span data-testid="status">{status}</span>
          <button
            onClick={() => {
              register("user@example.com", "password123").catch((err: unknown) => {
                capturedError = err
              })
            }}
          >
            register
          </button>
        </div>
      )
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RegisterErrorProbe />
        </AuthProvider>
      </QueryClientProvider>,
    )
    expect(await screen.findByTestId("status")).toHaveTextContent("anon")

    screen.getByText("register").click()

    await screen.findByTestId("status")
    expect(capturedError).toBeInstanceOf(AuthError)
    expect((capturedError as AuthError).status).toBe(409)
  })
})
