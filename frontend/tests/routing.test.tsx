import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, useLocation } from "react-router-dom"
import { http, HttpResponse } from "msw"
import { server } from "./msw/server"
import { setAccessToken } from "@/api/client"
import { AuthProvider } from "@/auth/AuthContext"
import { AppRoutes } from "@/AppRoutes"
import { Toaster } from "@/components/ui/sonner"

// Renders the current router location as text so tests can assert on
// pathname/search/hash without needing route-specific content to key off of.
function LocationSpy() {
  const location = useLocation()
  return <div data-testid="location-spy">{`${location.pathname}${location.search}${location.hash}`}</div>
}

// client.ts resolves its relative "/api" base against window.location.origin —
// derive the test base the same way so the two can't drift (see tests/client.test.ts).
const API_BASE = `${window.location.origin}/api`

const mockUser = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "user@example.com",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
}

// Fresh QueryClient per render so cache state can't leak between tests, mirroring
// the renderWithProviders pattern in tests/auth.test.tsx.
function renderWithProviders(initialEntries: string[], { withLocationSpy = false } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={initialEntries}>
          {withLocationSpy && <LocationSpy />}
          <AppRoutes />
        </MemoryRouter>
        <Toaster />
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe("AppRoutes", () => {
  beforeEach(() => {
    // Reset the module-level in-memory token so tests don't leak state into each other.
    setAccessToken(null)
  })

  it("redirects an anonymous user hitting a protected route to /login", async () => {
    // Default handler (tests/msw/handlers.ts) 401s /auth/refresh -> status "anon".
    renderWithProviders(["/documents"])

    expect(await screen.findByRole("heading", { name: /log in/i })).toBeInTheDocument()
  })

  it("logs in and lands on /chat, showing the AppShell with the user's email", async () => {
    server.use(
      http.post(`${API_BASE}/auth/login`, () =>
        HttpResponse.json({ access_token: "login-token", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
      // Task 12: ChatPage now renders a real thread (backed by useConversations
      // for the sidebar) instead of a placeholder — an empty list keeps this
      // test focused on routing/auth, not chat content.
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json([])),
    )

    const user = userEvent.setup()
    renderWithProviders(["/login"])

    await screen.findByRole("heading", { name: /log in/i })
    await user.type(screen.getByLabelText(/email/i), mockUser.email)
    await user.type(screen.getByLabelText(/password/i), "password123")
    await user.click(screen.getByRole("button", { name: /log in/i }))

    expect(await screen.findByText(/ask something about your notes/i)).toBeInTheDocument()
    expect(screen.getByText(mockUser.email)).toBeInTheDocument()
  })

  it("logs out and returns to /login", async () => {
    server.use(
      http.post(`${API_BASE}/auth/refresh`, () =>
        HttpResponse.json({ access_token: "t", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
      http.post(`${API_BASE}/auth/logout`, () => HttpResponse.json({ detail: "logged out" })),
      // See the comment in the test above — Task 12 replaced ChatPage's placeholder.
      http.get(`${API_BASE}/conversations`, () => HttpResponse.json([])),
    )

    const user = userEvent.setup()
    renderWithProviders(["/chat"])

    await screen.findByText(/ask something about your notes/i)
    await user.click(screen.getByRole("button", { name: /log out/i }))

    expect(await screen.findByRole("heading", { name: /log in/i })).toBeInTheDocument()
  })

  it("shows an error toast and stays on /login when credentials are rejected (401)", async () => {
    server.use(
      http.post(`${API_BASE}/auth/login`, () =>
        HttpResponse.json({ detail: "Incorrect email or password" }, { status: 401 }),
      ),
    )

    const user = userEvent.setup()
    renderWithProviders(["/login"])

    await screen.findByRole("heading", { name: /log in/i })
    await user.type(screen.getByLabelText(/email/i), mockUser.email)
    await user.type(screen.getByLabelText(/password/i), "wrong-password")
    await user.click(screen.getByRole("button", { name: /log in/i }))

    expect(await screen.findByText(/invalid email or password/i)).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: /log in/i })).toBeInTheDocument()
  })

  it("redirects an unknown path to the login screen via the catch-all route", async () => {
    // Anon user hitting a nonsense path should not see a blank page: catch-all
    // sends them to "/" -> "/chat" -> ProtectedRoute bounces to /login.
    renderWithProviders(["/nope"])

    expect(await screen.findByRole("heading", { name: /log in/i })).toBeInTheDocument()
  })

  it("preserves the query string on a deep link through the login redirect", async () => {
    server.use(
      http.post(`${API_BASE}/auth/login`, () =>
        HttpResponse.json({ access_token: "login-token", token_type: "bearer" }),
      ),
      http.get(`${API_BASE}/auth/me`, () => HttpResponse.json(mockUser)),
      // DocumentsPage now fetches the real list on mount (Task 9) — an empty
      // list keeps this test focused on routing, not document rendering.
      http.get(`${API_BASE}/documents`, () => HttpResponse.json([])),
    )

    const user = userEvent.setup()
    // Anon visits a protected URL with a query string; ProtectedRoute stashes
    // the full location (including search) in state.from for LoginPage to use.
    renderWithProviders(["/documents?course=cs101"], { withLocationSpy: true })

    await screen.findByRole("heading", { name: /log in/i })
    await user.type(screen.getByLabelText(/email/i), mockUser.email)
    await user.type(screen.getByLabelText(/password/i), "password123")
    await user.click(screen.getByRole("button", { name: /log in/i }))

    expect(await screen.findByRole("heading", { name: /^documents$/i })).toBeInTheDocument()
    expect(screen.getByTestId("location-spy")).toHaveTextContent("/documents?course=cs101")
  })
})
