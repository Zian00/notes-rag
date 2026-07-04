import { render, screen } from "@testing-library/react"
import App from "@/App"

// Smoke test: proves the Vitest + jsdom + @testing-library + @/ alias chain all work,
// including the QueryClientProvider/AuthProvider/Router wiring added in Tasks 6-7.
// The default MSW handler (tests/msw/handlers.ts) 401s /auth/refresh, so the
// on-mount silent refresh resolves to "anon" — ProtectedRoute then redirects
// the default "/" route to /chat, which redirects (anon) to /login. This is
// the minimal bar: confirm App mounts without throwing and settles on the
// login screen.
describe("App", () => {
  it("mounts without errors and redirects an anonymous user to /login", async () => {
    render(<App />)
    expect(await screen.findByRole("heading", { name: /log in/i })).toBeInTheDocument()
  })
})
